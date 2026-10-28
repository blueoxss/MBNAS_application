import json
p='/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
d=json.load(open(p)); S=d['components']['schemas']; P=d['paths']
d['info']['version']='1.2.0'
# --- RegistryEntry named component
reg_item=S['Health']['properties']['registry']['items']
S['RegistryEntry']=reg_item
S['Health']['properties']['registry']['items']={'$ref':'#/components/schemas/RegistryEntry'}
S['RegistryList']['properties']['registry']['items']={'$ref':'#/components/schemas/RegistryEntry'}
# --- Run: kind / parent / finalists
rp=S['Run']['properties']
rp['kind']={'enum':['SEARCH','FINAL_RETRAIN'],'description':'SEARCH = baseline + GA/Random search run; FINAL_RETRAIN = separate child run created by POST /runs/{id}/final-retrain.'}
rp['parent_run_id']={'anyOf':[{'type':'string'},{'type':'null'}]}
rp['finalists']={'type':'array','items':{'type':'object','properties':{'candidate_id':{'type':'string'},'source_evaluation_id':{'type':'string'},'arm':{'type':'string'},'search_seed':{'type':'integer'},'rung':{'type':'string'},'ap':{'type':'number'},'latency_ms':{'type':'number'},'selection_rule':{'type':'string'}},'required':['candidate_id','source_evaluation_id','arm','rung','selection_rule']},
 'description':'Frozen finalist list of a FINAL_RETRAIN run (highest L2 AP under the measured latency constraint, tie-break lower latency then stable candidate id, top_k per arm per seed). Empty list + reason NO_FINALIST is a valid report.'}
# --- Worker status vocabulary
S['Worker']['properties']['status']={'type':'string','enum':['idle','busy','offline','lost'],'description':'lost = heartbeat older than the lease TTL or process SIGKILLed.'}
# --- Protocol console.search.crossover_rate and benchmark.postprocess
cons=S['Protocol']['properties']['console']['properties']
cons['search']['properties']['crossover_rate']={'type':'number','minimum':0,'maximum':1,'description':'GA crossover probability per child (default 0.5 when absent; recorded in resolved).'}
cons['benchmark']['properties']['postprocess']={'type':'object','properties':{'conf':{'type':'number','minimum':0,'maximum':1},'iou':{'type':'number','minimum':0,'maximum':1},'max_det':{'type':'integer','minimum':1}},
 'description':'Operating point of the post-processing included in forward_and_postprocess latency (default conf 0.25, iou 0.7, max_det 300 — separate from the AP evaluation floor 0.001). Recorded in latency_scope.'}
# --- Artifact.matching keys, lineage.operation enum
S['Artifact']['properties']['matching']={'type':'object','properties':{'sample_id':{'type':'string'},'split':{'type':'string'},'checkpoint_sha256':{'type':'string'},'counts':{'type':'object','properties':{'tp':{'type':'integer'},'fp':{'type':'integer'},'fn':{'type':'integer'},'gt':{'type':'integer'}},'required':['tp','fp','fn','gt']},'sample_recall':{'anyOf':[{'type':'number'},{'type':'null'}],'description':'null when gt == 0 (N/A)'},'sample_precision':{'anyOf':[{'type':'number'},{'type':'null'}]},'rule':{'type':'string'}},
 'description':'For gt/tp/fn/fp: deterministic one-to-one matching metadata of the same sample and evaluation.'}
S['CandidateDetail']['properties']['lineage']={'type':'object','properties':{'operation':{'enum':['crossover','clone','mutation','random','baseline','final']},'crossover':{'type':'object'},'mutations':{'type':'array','items':{'type':'object'}},'repairs':{'type':'array','items':{'type':'object'}},'template':{'type':'string'}},'additionalProperties':True,
 'description':'operation crossover → 2 parents, clone/mutation → 1 parent, random/baseline → 0, final → the finalist source candidate.'}
# --- Final retrain endpoint
S['FinalRetrainRequest']={'type':'object','properties':{'idempotency_key':{'type':'string','minLength':1},'top_k_per_arm_per_seed':{'type':'integer','minimum':1},'budget_gpu_hours':{'type':'number','exclusiveMinimum':0},'reason':{'type':'string'}},'required':['idempotency_key','budget_gpu_hours'],'additionalProperties':False,
 'description':'Explicit user request for the separate final retraining phase of a COMPLETED search run. Finalists are chosen by the frozen rule; the resolved final recipe (scratch, epoch_target_total, horizon) comes from the parent resolved protocol. The final evaluation split is used only here.'}
P['/api/v1/runs/{run_id}/final-retrain']={'post':{'summary':'Create the separate FINAL_RETRAIN child run for a COMPLETED search run','parameters':[{'name':'run_id','in':'path','required':True,'schema':{'type':'string'}},{'name':'Idempotency-Key','in':'header','required':False,'schema':{'type':'string'}}],
 'requestBody':{'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/FinalRetrainRequest'}}}},
 'responses':{'200':{'description':'Child run created (or the existing one for the same idempotency key)','content':{'application/json':{'schema':{'$ref':'#/components/schemas/RunCreated'}}}},
  '401':{'description':'Authentication required'},'403':{'description':'Not authorized'},'404':{'description':'Run not found'},
  '409':{'description':'INVALID_STATE (parent not COMPLETED), NO_FINALIST (no eligible L2 finalist — reported, never substituted by a lower rung), IDEMPOTENCY_CONFLICT, BUDGET_INVALID','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}},
  '422':{'description':'Semantic validation failed','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}}}}}
S['RunCreated']['properties']['parent_run_id']={'anyOf':[{'type':'string'},{'type':'null'}]}
S['RunCreated']['properties']['finalist_count']={'type':'integer','minimum':0}
# --- CandidateEvaluation arm 'Final' already in enum; add rung note
S['CandidateEvaluation']['properties']['rung']['description']='L0/L1/L2 for search rungs; FINAL for final retraining rows (comparison_group=final, split = final_eval).'
d['x-contract-changelog'].insert(0,{'version':'1.2.0','date':'2026-10-07','changes':[
 'POST /runs/{id}/final-retrain (FinalRetrainRequest → RunCreated with parent_run_id/finalist_count); Run.kind SEARCH|FINAL_RETRAIN, Run.parent_run_id, Run.finalists; final rows use arm Final, rung FINAL, comparison_group final.',
 'RegistryEntry promoted to a named component (no field change).',
 'Worker.status enum idle|busy|offline|lost (documents existing values).',
 'Protocol.console.search.crossover_rate; Protocol.console.benchmark.postprocess {conf,iou,max_det} (latency operating point, default 0.25/0.7/300).',
 'Artifact.matching keys documented (counts{tp,fp,fn,gt}, sample_recall null when gt==0); CandidateDetail.lineage.operation enum.']})
json.dump(d,open(p,'w'),ensure_ascii=False,indent=1)
import re
txt=json.dumps(d); refs=set(re.findall(r'"\$ref": "#/components/schemas/([A-Za-z]+)"',txt)); print('refs ok:', [r for r in refs if r not in S]==[], 'version', d['info']['version'])