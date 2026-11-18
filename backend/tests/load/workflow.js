import http from 'k6/http';
import {check, sleep} from 'k6';
import {Counter} from 'k6/metrics';

const completed = new Counter('completed_workflows');

export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  vus: 5, duration: '2m',
  thresholds: {http_req_failed:['rate<0.01'],http_req_duration:['p(95)<1000','p(99)<2000'],checks:['rate>0.99'],completed_workflows:['count>0']},
};
const base = __ENV.BASE_URL || 'http://127.0.0.1:18081';
const password = __ENV.DEMO_PASSWORD || 'Demo-only-science2026!';
let owner, candidate, sequence=0, run;
export function setup() {
  const response=http.get(base+'/api/v1/dictionaries/research_areas');
  if(response.status!==200 || !response.json('items').length) throw new Error('Seed dictionaries before workload');
  return {area:response.json('items')[0].id, run:`${Date.now()}-${Math.random().toString(36).slice(2,12)}`};
}
function login(number) {
  const response=http.post(base+'/api/v1/auth/login',JSON.stringify({email:`student${String(number).padStart(4,'0')}@demo.example.org`,password}),{headers:{'Content-Type':'application/json'}});
  check(response,{'demo login succeeds':r=>r.status===200});
  return response.status===200 ? response.json('access_token') : null;
}
function command(method,path,body,token,status=200) {
  const response=http.request(method,base+path,body===null?null:JSON.stringify(body),{headers:{'Authorization':`Bearer ${token}`,'Content-Type':'application/json','Idempotency-Key':`load-${run}-${__VU}-${__ITER}-${sequence++}`},tags:{endpoint:path.replace(/[0-9a-f-]{36}/g,':id')}});
  check(response,{[`${method} expected status`]:r=>r.status===status});
  return response.status===status && response.body ? response.json() : null;
}
export default function(data) {
  run=data.run;
  if(!owner) owner=login(900+__VU);
  if(!candidate) candidate=login(500+__VU);
  if(!owner || !candidate) {sleep(2);return;}
  const project=command('POST','/api/v1/projects',{title:`Нагрузочное исследование ${run}/${__VU}/${__ITER}`,description:'Проверка записи и формирования команды под нагрузкой',research_goal:'Проверить транзакционные команды',area_id:data.area,team_size:3},owner,201);
  if(!project) return;
  const root=`/api/v1/projects/${project.id}`;
  if(!command('POST',root+'/publish',null,owner)) return;
  const application=command('POST',root+'/applications',{message:'Могу помочь с исследованием и анализом данных'},candidate,201);
  if(!application) return;
  if(!command('POST',root+`/applications/${application.id}/accept`,{},owner)) return;
  const detail=http.get(base+root,{headers:{Authorization:`Bearer ${candidate}`}});
  const team=check(detail,{'team has exactly two members':r=>r.status===200 && r.json('members_count')===2});
  const archived=command('DELETE',root,null,owner);
  if(team && archived) completed.add(1);
  sleep(2+Math.random());
}
