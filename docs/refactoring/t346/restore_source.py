"""Restore only this refactoring's old runtime bytes; default is a checked dry run."""
import argparse,hashlib,json,os,tarfile,tempfile
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');ns=p.parse_args()
here=Path(__file__).resolve().parent;data=json.loads((here/'deployment.json').read_text());root=Path(data['original_root']);archive=Path(data['archive'])
def sha(value):return hashlib.sha256(value).hexdigest()
assert sha(archive.read_bytes())==data['archive_sha256'],'Backup archive changed'
with tarfile.open(archive,'r:gz') as tar:
 old={name:tar.extractfile(name).read() for name in data['files']}
 for name,info in data['files'].items():
  assert sha(old[name])==info['before'],('Archive entry changed',name)
  assert sha((root/name).read_bytes()) in (info['before'],info['after']),('Later user edit; refusing restore',name)
 if ns.apply:
  # Restoring core first leaves new DB/config implementations available to either facade.
  order=sorted(old,key=lambda name:(name!='src/graft/_core.py',name))
  for name in order:
   dst=root/name
   if sha(dst.read_bytes())==data['files'][name]['before']:continue
   with tempfile.NamedTemporaryFile(dir=dst.parent,prefix='.'+dst.name+'.restore-',delete=False) as stream:
    pending=Path(stream.name);stream.write(old[name]);stream.flush();os.fsync(stream.fileno())
   os.chmod(pending,dst.stat().st_mode & 0o777);os.replace(pending,dst)
print(json.dumps({'applied':ns.apply,'checked_files':len(old),'root':str(root),'production_db_or_manifest_modified':False}))
