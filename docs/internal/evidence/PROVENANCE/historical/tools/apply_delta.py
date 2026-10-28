import json,hashlib,os,shutil,subprocess,datetime,sys
L='/home/mgjeong/Desktop/etri_hd/etri_nas'; T='/home/mgjeong/Desktop/etri_hd/mbnas_application'; P=T+'/docs/internal/provenance'
d=json.load(open(sys.argv[1]))
now=datetime.datetime.now(datetime.timezone.utc); tag=now.astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%dT%H%M%S')
def git(*a): return subprocess.run(['git','-C',L,'--no-optional-locks',*a],capture_output=True,text=True).stdout
head=git('rev-parse','HEAD').strip()
man=[json.loads(l) for l in open(P+'/SOURCE_SNAPSHOT_MANIFEST.jsonl')]
idx={r['source_relpath']:i for i,r in enumerate(man)}
def role(rel):
    if rel.startswith('application/MBNAS_server/contracts'): return 'contracts'
    if rel.startswith('application/MBNAS_server/tests'): return 'server-tests'
    if rel.startswith('application/MBNAS_web/tests'): return 'web-tests'
    raise SystemExit(rel)
items=[(c[0],c[1]) for c in d['changed']]+[(n,None) for n in d['new']]
out=[]
for rel,sup in items:
    src=os.path.join(L,rel); b=open(src,'rb').read(); h=hashlib.sha256(b).hexdigest()
    dst=os.path.join(T,'build/source_snapshot',rel); os.makedirs(os.path.dirname(dst),exist_ok=True)
    open(dst,'wb').write(b)
    assert hashlib.sha256(open(dst,'rb').read()).hexdigest()==h
    st=git('status','--porcelain','--',rel)
    gs='untracked' if st.startswith('??') else ('tracked_modified' if st.strip() else 'tracked_clean')
    ib=git('ls-files','-s','--',rel).split(); hb=git('ls-tree','HEAD','--',rel).split()
    row={'bytes':len(b),'copied_at':now.isoformat(),'delta':3,
         'git_blob_sha1':subprocess.run(['git','hash-object','--no-filters',src],capture_output=True,text=True).stdout.strip(),
         'git_status':gs,'head_blob_sha1':hb[2] if hb else None,'index_blob_sha1':ib[1] if ib else None,
         'license':'no LICENSE file in etri_nas root; ETRI_NAS research code, rights review pending',
         'role_group':man[idx[rel]]['role_group'] if rel in idx else role(rel),'sha256':h,
         'snapshot_relpath':'build/source_snapshot/'+rel,
         'source_mtime':datetime.datetime.fromtimestamp(os.stat(src).st_mtime,datetime.timezone.utc).isoformat(),
         'source_relpath':rel,'supersedes_sha256':sup}
    out.append(row)
    if rel in idx: man[idx[rel]]=row
    else: man.append(row)
with open(P+f'/SOURCE_SNAPSHOT_DELTA_{tag}.jsonl','w') as f:
    for r in out: f.write(json.dumps(r,sort_keys=True)+'\n')
with open(P+'/SOURCE_SNAPSHOT_MANIFEST.jsonl','w') as f:
    for r in man: f.write(json.dumps(r,sort_keys=True)+'\n')
s=json.load(open(P+'/SOURCE_SNAPSHOT_SUMMARY.json'))
s['deltas'].append({'tag':tag,'at':now.isoformat(),'head':head,'files':[r['source_relpath'] for r in out],
 'reason':'concurrent session Phase G: contract 1.4.3, structure gate fix, DD-44/45 web, docs'})
json.dump(s,open(P+'/SOURCE_SNAPSHOT_SUMMARY.json','w'),indent=1,ensure_ascii=False)
print(tag,len(out),len(man))
