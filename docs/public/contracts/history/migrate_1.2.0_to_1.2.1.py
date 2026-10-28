import json
p='/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d=json.load(open(p)); S=d['components']['schemas']; P=d['paths']
d['info']['version']='1.2.1'
lin=S['CandidateDetail']['properties']['lineage']['properties']
lin['template']={'type':'string','description':'Seed template id (e.g. yolo11n / yolo26n native, cnn_backbone, transformer_backbone variant id).'}
lin['template_detail']={'type':'object','properties':{'id':{'type':'string'},'family':{'type':'string'},'variant':{'type':'string'}},'description':'Structured template reference {id, family, variant} of the searched genotype.'}
S['Run']['properties']['kind']['description']+=' Absent on rows written before contract 1.2.0 means SEARCH.'
S['Error']['properties']['code']['description']='Machine code. Server-side contract violations of a response are reported as 500 RESPONSE_CONTRACT_VIOLATION (the response is withheld rather than sent malformed).'
for path,ops in P.items():
    for m,op in ops.items():
        if m in ('get','post') and '500' not in op.get('responses',{}):
            op['responses']['500']={'description':'RESPONSE_CONTRACT_VIOLATION or internal error','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}}
d['x-contract-changelog'].insert(0,{'version':'1.2.1','date':'2026-10-07','changes':['CandidateDetail.lineage.template documented as the template id string; template_detail {id,family,variant} added.','500 RESPONSE_CONTRACT_VIOLATION documented on every operation.','Run.kind absence on pre-1.2.0 rows documented as SEARCH.']})
json.dump(d,open(p,'w'),ensure_ascii=False,indent=1)
print('contract 1.2.1 written')