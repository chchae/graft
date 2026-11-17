"""Connection-close errors retain calculators; scientific/protocol errors propagate."""
import multiprocessing
import signal
import socket
import struct
from unittest.mock import Mock

import pytest

from graft.stages import combichem_compute as compute, combichem_slurm_pool as pool


@pytest.fixture(autouse=True)
def preserve_sigterm():
    original=signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM,original)


@pytest.mark.parametrize('write_error',[BrokenPipeError,ConnectionResetError])
@pytest.mark.parametrize('close_error',[BrokenPipeError,ConnectionResetError])
def test_disconnected_owner_during_write_and_stream_close_keeps_calculator_alive(
        tmp_path,monkeypatch,write_error,close_error):
    class Stream:
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):raise close_error('flush after disconnected owner')
        def readline(self,*args):return 'token\n'
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,value):pass
        def makefile(self,*args,**kw):return Stream()
    listener=Mock()
    listener.accept.return_value=(Connection(),None)
    def serve(*args):
        (tmp_path/'stop').touch()
        raise write_error('owner stopped after target')
    monkeypatch.setattr(compute,'serve',serve)
    pool.calculate(listener,tmp_path,'token')
    listener.accept.assert_called_once()


def test_calculator_accepts_a_new_owner_after_disconnected_stream_close(tmp_path,monkeypatch):
    connections=[]
    class Stream:
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
        def close(self):
            if len(connections)==1:raise BrokenPipeError('first owner closed')
        def readline(self,*args):return 'token\n'
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,value):pass
        def makefile(self,*args,**kw):return Stream()
    def accept():
        connections.append(1)
        assert len(connections)<=2
        return Connection(),None
    listener=Mock();listener.accept.side_effect=accept
    def serve(*args):
        if len(connections)==1:raise ConnectionResetError('first owner gone')
        (tmp_path/'stop').touch()
    monkeypatch.setattr(compute,'serve',serve)
    pool.calculate(listener,tmp_path,'token')
    assert len(connections)==2


@pytest.mark.parametrize('close_error',[None,BrokenPipeError,ConnectionResetError])
def test_protocol_errors_still_fail_the_calculator(tmp_path,monkeypatch,close_error):
    stream=Mock()
    stream.__enter__=Mock(return_value=stream)
    stream.__exit__=Mock(return_value=False)
    stream.readline.return_value='token\n'
    if close_error:
        stream.close.side_effect=close_error('close while protocol failed')
        stream.__exit__.side_effect=lambda *a:stream.close()
    connection=Mock()
    connection.__enter__=Mock(return_value=connection)
    connection.__exit__=Mock(return_value=False)
    connection.makefile.return_value=stream
    listener=Mock();listener.accept.return_value=(connection,None)
    monkeypatch.setattr(compute,'serve',Mock(side_effect=ValueError('corrupt protocol')))
    with pytest.raises(ValueError,match='corrupt protocol'):
        pool.calculate(listener,tmp_path,'token')


def test_real_socket_reset_during_buffered_close_allows_next_owner(tmp_path,monkeypatch):
    context=multiprocessing.get_context('fork')
    ready,released=context.Event(),context.Event()
    calls=[]
    def serve(incoming,outgoing):
        calls.append(1)
        if len(calls)==1:
            outgoing.write('buffered reply without flush')
            ready.set()
            assert released.wait(5)
        else:
            outgoing.write('second-owner-ready\n');outgoing.flush()
            (tmp_path/'stop').touch()
    monkeypatch.setattr(compute,'serve',serve)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',0));listener.listen(2);listener.settimeout(.2)
        process=context.Process(target=pool.calculate,args=(listener,tmp_path,'token'))
        process.start()
        try:
            first=socket.create_connection(listener.getsockname(),timeout=5)
            first.sendall(b'token\n')
            assert ready.wait(5)
            first.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
            first.close()
            released.set()
            with socket.create_connection(listener.getsockname(),timeout=5) as second:
                second.sendall(b'token\n')
                assert second.recv(1024)==b'second-owner-ready\n'
            process.join(5)
            assert process.exitcode==0
        finally:
            released.set()
            (tmp_path/'stop').touch()
            if process.is_alive():process.terminate()
            process.join(5)
