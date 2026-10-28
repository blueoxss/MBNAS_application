import json, copy, datetime
src='/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_Server_Handoff_20261006/NAS_Console_API_Contract_20261006.json'
d=json.load(open(src))
S=d['components']['schemas']
P=d['paths']
d['info']['version']='1.1.0'
d['info']['title']='MBNAS Server API'
d['info']['description']=('Canonical web/server contract for MBNAS_server (package mbnas_server). Derived from '
 'NAS_Console_API_Contract_20261006.json v1.0.0 with backward-compatible extensions (see x-contract-changelog). '
 'Candidate row id is evaluation_id; candidate_id is stable across promotions. Values in examples are not measured results.')
d['servers']=[{'url':'http://127.0.0.1:8000','description':'local development (Vite same-origin proxy /api -> this)'},
              {'url':'https://YOUR_AUTHORIZED_API_ORIGIN','description':'production HTTPS origin; exact frontend origin CORS allowlist required'}]
# ---- one spelling: CANCELLED
st=S['CandidateEvaluation']['properties']['status']['enum']
S['CandidateEvaluation']['properties']['status']['enum']=[('CANCELLED' if x=='CANCELED' else x) for x in st]
S['CandidateEvaluation']['properties']['candidate_status']={'type':'string','enum':S['CandidateEvaluation']['properties']['status']['enum'],
  'description':'Mutable architecture candidate state; measurement row status remains EVALUATED after its immutable commit.'}
cp=S['CandidateEvaluation']['properties']
cp['progress']={'type':'object','properties':{'epoch':{'type':'integer','minimum':0},'epoch_target_total':{'type':'integer','minimum':0},
  'learning_rate':{'anyOf':[{'type':'number'},{'type':'null'}]},'global_step':{'type':'integer','minimum':0},'samples_seen':{'type':'integer','minimum':0}},
  'additionalProperties':True,'description':'Live training progress of the provisional row; absent on immutable EVALUATED rows except as history.'}
cp['attempt']={'type':'integer','minimum':1}
cp['job_id']={'anyOf':[{'type':'string'},{'type':'null'}]}
cp['revision']={'type':'integer','minimum':1,'description':'Superseding revision number of this evaluation record.'}
cp['architecture_id']={'type':'string','description':'Alias of architecture_hash (structure identity, not trial identity).'}
cp['namespace']={'type':'string','description':'Run namespace, e.g. default or pilot_smoke.'}
cp['execution_mode']={'enum':['PILOT','REGISTERED_SEARCH','HISTORICAL_REPRODUCTION']}
cp['source_kind_legend']={'type':'object','description':'Optional legend of block source kinds used in blocks[].'}
cp['raw_metrics']={'type':'object','description':'All raw evaluator outputs (ap_50, counts, latency statistics per scope, size definitions).'}
cp['rewards']={'type':'object','properties':{'latency_reward':{'anyOf':[{'type':'number'},{'type':'null'}]},'size_reward':{'anyOf':[{'type':'number'},{'type':'null'}]}},'additionalProperties':True}
cp['fitness_weights']={'type':'object','description':'Named weights of the source rung applied to this row.'}
# Run extensions
rp=S['Run']['properties']
rp['status']={'type':'string','enum':['QUEUED','RUNNING','PAUSE_REQUESTED','PAUSED','RESUMING','CANCEL_REQUESTED','COMPLETED','FAILED','CANCELLED']}
rp['namespace']={'type':'string'}
rp['execution_mode']={'enum':['PILOT','REGISTERED_SEARCH','HISTORICAL_REPRODUCTION']}
rp['purpose']={'type':'string','description':'e.g. SMOKE for PILOT_SMOKE preset runs.'}
rp['preset']={'anyOf':[{'type':'string'},{'type':'null'}]}
rp['updated_at']={'type':'string','format':'date-time'}
rp['terminal']={'type':'boolean'}
rp['source_commit']={'anyOf':[{'type':'string'},{'type':'null'}],'description':'Server source identifier (git SHA or source tree sha256).'}
rp['protocol_canonicalization']={'type':'string','description':'Canonical JSON + hash algorithm version, e.g. mbnas-canon/1 sha256.'}
rp['budget']={'type':'object','description':'Frozen budget per arm/seed in GPU-seconds and admission policy.'}
rp['cancel']={'type':'object','description':'Cancel tracking: requested_at, scope, kill/cleanup state.'}
rp['costs']['properties']['failed_gpu_seconds']={'type':'number','minimum':0}
rp['costs']['properties']['retry_gpu_seconds']={'type':'number','minimum':0}
rp['costs']['properties']['profile_gpu_seconds']={'type':'number','minimum':0}
# Worker extensions
wp=S['Worker']['properties']
for k,v in {'device_uuid':{'anyOf':[{'type':'string'},{'type':'null'}]},'device_name':{'anyOf':[{'type':'string'},{'type':'null'}]},
  'job_id':{'anyOf':[{'type':'string'},{'type':'null'}]},'run_id':{'anyOf':[{'type':'string'},{'type':'null'}]},'attempt':{'anyOf':[{'type':'integer'},{'type':'null'}]},
  'global_step':{'anyOf':[{'type':'integer'},{'type':'null'}]},'rung':{'anyOf':[{'type':'string'},{'type':'null'}]},'pid':{'anyOf':[{'type':'integer'},{'type':'null'}]},
  'lease_expires_at':{'anyOf':[{'type':'string','format':'date-time'},{'type':'null'}]},'power_w':{'anyOf':[{'type':'number'},{'type':'null'}]},'sm_clock_mhz':{'anyOf':[{'type':'number'},{'type':'null'}]}}.items():
    wp[k]=v
# Health extensions
hp=S['Health']['properties']
hp['server_time']={'type':'string','format':'date-time'}
hp['allowlists']={'type':'object','properties':{'dataset_roots':{'type':'array','items':{'type':'string'}},'output_roots':{'type':'array','items':{'type':'string'}}}}
hp['presets']={'type':'array','items':{'$ref':'#/components/schemas/PresetSummary'}}
hp['worker_count']={'type':'integer','minimum':0}
hp['registry']['items']['properties']['evidence']={'type':'object','description':'version/commit/import class/config+weights sha256/native loss/init/train/resume/export/device evidence.'}
# ValidateResponse extensions
vp=S['ValidateResponse']['properties']
vp['warnings']={'type':'array','items':{'type':'object','properties':{'field':{'type':'string'},'message':{'type':'string'},'code':{'type':'string'}},'required':['field','message']}}
vp['resolved_diff']={'type':'array','items':{'type':'object','properties':{'field':{'type':'string'},'requested':{},'resolved':{},'reason':{'type':'string'}},'required':['field']},
  'description':'Every field whose resolved value differs from the request (e.g. optimizer=auto → actual lr, requested vs resolved image shape, execution_mode defaulted to PILOT).'}
vp['validation_id']={'anyOf':[{'type':'string'},{'type':'null'}],'description':'Server-side id of the frozen validated snapshot; Run may send it to prove non-stale validation.'}
# RunRequest: allow validation_id
S['RunRequest']['properties']['validation_id']={'type':'string','minLength':1}
S['RunRequest']['additionalProperties']=False
# Protocol console extension
cons=S['Protocol']['properties']['console']['properties']
cons['preset']={'anyOf':[{'type':'string'},{'type':'null'}],'description':'e.g. PILOT_SMOKE; absent means the default baseline14 protocol.'}
cons['purpose']={'anyOf':[{'type':'string'},{'type':'null'}],'description':'SMOKE for pilot smoke runs.'}
cons['run_namespace']={'anyOf':[{'type':'string'},{'type':'null'}]}
cons['label']={'anyOf':[{'type':'string'},{'type':'null'}]}
# New schemas
S['PresetSummary']={'type':'object','properties':{'id':{'type':'string'},'name':{'type':'string'},'purpose':{'type':'string'},'execution_mode':{'type':'string'},
  'namespace':{'type':'string'},'description':{'type':'string'}},'required':['id','name','purpose','execution_mode','namespace']}
S['Preset']={'allOf':[{'$ref':'#/components/schemas/PresetSummary'},{'type':'object','properties':{'protocol':{'$ref':'#/components/schemas/Protocol'}},'required':['protocol']}]}
S['RunList']={'type':'object','properties':{'runs':{'type':'array','items':{'$ref':'#/components/schemas/Run'}}},'required':['runs']}
S['CandidateDetail']={'type':'object','properties':{'candidate_id':{'type':'string'},'run_id':{'type':'string'},'name':{'type':'string'},'arm':{'type':'string'},
  'family':{'type':'string'},'architecture_hash':{'type':'string'},'architecture_yaml':{'anyOf':[{'type':'string'},{'type':'null'}]},
  'architecture_path':{'anyOf':[{'type':'string'},{'type':'null'}]},'blocks':{'type':'array','items':{'$ref':'#/components/schemas/Block'}},
  'parents':{'type':'array','items':{'type':'string'}},'parent_evaluation_ids':{'type':'array','items':{'type':'string'}},
  'lineage':{'type':'object','description':'operation (crossover|clone|mutation|random|baseline), crossover mask/cut, mutation events (before/after), repair events (before/after/reason), source template.'},
  'evaluations':{'type':'array','items':{'$ref':'#/components/schemas/CandidateEvaluation'}},'checkpoints':{'type':'array','items':{'type':'object'}},
  'cost':{'type':'object'},'gate_history':{'type':'array','items':{'type':'object'}},'candidate_status':{'type':'string'},'audit':{'type':'object','description':'config estimate vs built-model operator audit.'}},
  'required':['candidate_id','run_id','name','arm','family','architecture_hash','blocks','parents','evaluations','candidate_status']}
S['RegistryList']={'type':'object','properties':{'registry':{'type':'array','items':S['Health']['properties']['registry']['items']}},'required':['registry']}
# New paths
def resp(ref,desc='Successful response'):
    return {'200':{'description':desc,'content':{'application/json':{'schema':{'$ref':ref}}}},'401':{'description':'Authentication required'},'403':{'description':'Not authorized'},
            '404':{'description':'Not found','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}}}
P['/api/v1/protocols/presets']={'get':{'summary':'Server presets (default baseline14 example protocol, PILOT_SMOKE)','responses':resp('#/components/schemas/PresetList')}}
S['PresetList']={'type':'object','properties':{'presets':{'type':'array','items':{'$ref':'#/components/schemas/Preset'}}},'required':['presets']}
P['/api/v1/registry']={'get':{'summary':'Baseline registry with capability evidence','responses':resp('#/components/schemas/RegistryList')}}
P['/api/v1/runs']['get']={'summary':'List runs (newest first) for loading an existing run','parameters':[{'name':'namespace','in':'query','required':False,'schema':{'type':'string'}},{'name':'limit','in':'query','required':False,'schema':{'type':'integer','minimum':1,'maximum':500}}],'responses':resp('#/components/schemas/RunList')}
P['/api/v1/runs/{run_id}']={'get':{'summary':'Run object with resolved_protocol and costs','parameters':[{'name':'run_id','in':'path','required':True,'schema':{'type':'string'}}],'responses':resp('#/components/schemas/Run')}}
P['/api/v1/runs/{run_id}/candidates/{candidate_id}']={'get':{'summary':'Candidate detail: lineage, blocks, all immutable evaluations, checkpoints, cost, gates','parameters':[{'name':'run_id','in':'path','required':True,'schema':{'type':'string'}},{'name':'candidate_id','in':'path','required':True,'schema':{'type':'string'}}],'responses':resp('#/components/schemas/CandidateDetail')}}
P['/api/v1/runs/{run_id}/artifacts/{artifact_id}/refresh']={'post':{'summary':'Issue a fresh short-lived signed URL / metadata for an expired artifact reference','parameters':[{'name':'run_id','in':'path','required':True,'schema':{'type':'string'}},{'name':'artifact_id','in':'path','required':True,'schema':{'type':'string'}}],'responses':resp('#/components/schemas/Artifact')}}
# artifact GET: document headers & states
P['/api/v1/runs/{run_id}/artifacts/{artifact_id}']['get']['description']=('Registry-mediated artifact access. Bearer required unless a valid signed query (sig, exp) is present. '
 'Responses: 200 bytes with X-Artifact-Sha256/Content-Type; 401/403 ACCESS_DENIED; 404 MISSING; 409 NOT_READY; 410 EXPIRED (signed url). Never accepts filesystem paths.')
P['/api/v1/runs/{run_id}/artifacts/{artifact_id}']['get']['responses']['409']={'description':'Artifact not ready'}
P['/api/v1/runs/{run_id}/artifacts/{artifact_id}']['get']['responses']['410']={'description':'Signed URL expired'}
P['/api/v1/runs/{run_id}/events']['get']['description']=('Bearer-authenticated fetch SSE (Content-Type text/event-stream, Cache-Control no-cache, X-Accel-Buffering no). '
 'Wire: id:<seq>, event:<type>, data:<json Event>. Replay from after_seq / Last-Event-ID (run-local monotonic, committed before publish). '
 'Heartbeat comment every 15s. If the cursor is older than the replay retention the stream emits snapshot.required.')
P['/api/v1/runs/{run_id}/export']['get']['responses']['200']['content']={'application/zip':{'schema':{'type':'string','format':'binary'}}}
# Artifact states
S['Artifact']['properties']['state']={'enum':['READY','NOT_READY','EXPIRED','ACCESS_DENIED','MISSING','LOAD_FAILED']}
S['Artifact']['properties']['refresh_url']={'anyOf':[{'type':'string'},{'type':'null'}]}
S['Artifact']['properties']['evaluation_id']={'anyOf':[{'type':'string'},{'type':'null'}]}
S['Artifact']['properties']['matching']={'type':'object','description':'For gt/tp/fn/fp: sample_id, split, checkpoint sha, counts {tp,fp,fn,gt}, sample recall/precision (null when GT=0), matching rule.'}
# Generation: add group key parts
gp=S['Generation']['properties']
gp['group_key']={'type':'string','description':'arm+search_seed+generation+rung+comparison_group'}
gp['representative_rule']={'type':'string','description':'current-rung highest fitness → lower latency → stable candidate id'}
# Event: run-local seq; add heartbeat note
S['Event']['description']='SSE data JSON. seq is run-local and monotonic; duplicates/out-of-order must be deduplicated by the client; gaps require a snapshot.'
d['x-contract-changelog']=[
 {'version':'1.1.0','date':'2026-10-06','changes':[
  'One status spelling CANCELLED for runs and candidates (importer maps legacy CANCELED).',
  'Run: enum status, namespace, execution_mode, purpose, preset, updated_at, terminal, source_commit, protocol_canonicalization, budget, cancel; costs add failed/retry/profile seconds.',
  'CandidateEvaluation: progress, attempt, job_id, revision, architecture_id, namespace, execution_mode, raw_metrics, rewards, fitness_weights.',
  'Worker: device_uuid/name, job_id, run_id, attempt, global_step, rung, pid, lease_expires_at, power_w, sm_clock_mhz.',
  'Health: server_time, allowlists, presets, worker_count. ValidateResponse: warnings, resolved_diff, validation_id. RunRequest: validation_id.',
  'Protocol.console: preset, purpose, run_namespace, label.',
  'New: GET /protocols/presets, GET /registry, GET /runs, GET /runs/{id}, GET /runs/{id}/candidates/{cid}, POST /runs/{id}/artifacts/{aid}/refresh.',
  'Artifact: state, refresh_url, evaluation_id, matching. Generation: group_key, representative_rule.']}]
d['x-status-vocabulary']={'run':rp['status']['enum'],'candidate':S['CandidateEvaluation']['properties']['status']['enum'],'terminal_run':['COMPLETED','FAILED','CANCELLED'],
 'nonterminal_run_policy_locked':['QUEUED','RUNNING','PAUSE_REQUESTED','PAUSED','RESUMING','CANCEL_REQUESTED']}
out='/ABSOLUTE/PATH/TO/ETRI_NAS/application/MBNAS_server/contracts/openapi.json'
json.dump(d,open(out,'w'),ensure_ascii=False,indent=1)
print('written',out)
# sanity: all $refs resolve
import re
txt=json.dumps(d)
refs=set(re.findall(r'"\$ref": "#/components/schemas/([A-Za-z]+)"',txt))
missing=[r for r in refs if r not in S]
print('refs',len(refs),'missing',missing)