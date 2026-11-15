from collections import Counter
import json,re,xml.etree.ElementTree as ET
from pathlib import Path
s=json.loads(Path('/tmp/graft-t346-active.json').read_text());R=Path(s['run'])
def nodes(label):
 out={}
 for node in ET.parse(R/(label+'.xml')).iter('testcase'):
  key=node.attrib['classname'].replace('.','/')+'.py::'+node.attrib['name'];child=next((x for x in node if x.tag in ('failure','error','skipped')),None);status=child.tag if child is not None else 'passed';text=child.text or '' if child is not None else '';message=child.attrib.get('message','') if child is not None else ''
  text=re.sub(r'/tmp/graft-t346-[^/]+/test-tmp/[^/]+/pytest-of-[^/\s]+/pytest-\d+','<PYTEST>',text)
  text=re.sub(r'0x[0-9a-fA-F]+','<ADDR>',text);text=re.sub(r'\.\.\.[0-9a-fA-F]{5,}>','...<ADDR>>',text);message=re.sub(r'0x[0-9a-fA-F]+','<ADDR>',message)
  if status=='failure':
   header=re.search(r'^    def test_',text,re.M)
   if header:text=text[header.start():]
  out[key]=dict(status=status,message=message,body=text)
 return out
reports={}
for before,after in [('baseline','t3-full'),('baseline','t4-full-recheck'),('t6-scaffold-before','t6-scaffold'),('t6-glide-before','t6-glide'),('t6-driver-before','t6-driver'),('baseline','final-full')]:
 a,b=nodes(before),nodes(after);changed={k:dict(before=v,after=b.get(k)) for k,v in a.items() if b.get(k)!=v};added={k:v for k,v in b.items() if k not in a}
 assert not changed, list(changed)
 assert all(v['status']=='passed' for v in added.values())
 reports[after]=dict(before_count=len(a),after_count=len(b),outcomes=dict(Counter(v['status'] for v in b.values())),changed=changed,added=added,failures={k:v for k,v in b.items() if v['status']=='failure'})
assert len(reports['final-full']['added'])==35
(R/'comparison.json').write_text(json.dumps(reports,indent=2));print({k:dict(before=v['before_count'],after=v['after_count'],changed=len(v['changed']),added=len(v['added']),outcomes=v['outcomes']) for k,v in reports.items()});print(dict(Counter(k.split('::')[0] for k in reports['final-full']['added'])))
