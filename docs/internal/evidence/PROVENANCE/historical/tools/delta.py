import json,hashlib,os,sys
L='/home/mgjeong/Desktop/etri_hd/etri_nas'; T='/home/mgjeong/Desktop/etri_hd/mbnas_application'
rows=[json.loads(l) for l in open(T+'/docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl')]
known={r['source_relpath'] for r in rows}
ch=[];miss=[]
for r in rows:
    p=os.path.join(L,r['source_relpath'])
    if not os.path.exists(p): miss.append(r['source_relpath']);continue
    h=hashlib.sha256(open(p,'rb').read()).hexdigest()
    if h!=r['sha256']: ch.append((r['source_relpath'],r['sha256'],h,r['role_group']))
new=[]
for d in ['application/MBNAS_server/src','application/MBNAS_server/tests','application/MBNAS_server/contracts','application/MBNAS_web/src','application/MBNAS_web/tests','application/MBNAS_web/e2e']:
    for root,ds,fs in os.walk(os.path.join(L,d)):
        ds[:]=[x for x in ds if x not in('__pycache__','node_modules','.pytest_cache')]
        for f in fs:
            rel=os.path.relpath(os.path.join(root,f),L)
            if rel not in known and not f.endswith('.pyc'): new.append(rel)
json.dump({'changed':ch,'missing':miss,'new':new},open(sys.argv[1],'w'),indent=1)
print(len(ch),len(miss),len(new))
for c in ch: print('CH',c[0])
for n in new: print('NEW',n)
for m in miss: print('MISS',m)
