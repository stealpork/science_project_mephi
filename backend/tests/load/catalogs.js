import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const mode = __ENV.LOAD_PROFILE || 'smoke';
const scale = Number(__ENV.VU_SCALE || '1');
const vus = count => Math.round(count * scale);
const profiles = {
  smoke: { executor: 'constant-vus', vus: vus(5), duration: '30s' },
  baseline: { executor: 'constant-vus', vus: vus(20), duration: '2m' },
  stress: { executor: 'ramping-vus', startVUs: 0, stages: [{duration:'2m',target:vus(20)},{duration:'5m',target:vus(50)},{duration:'5m',target:vus(100)},{duration:'2m',target:0}] },
  soak: { executor: 'constant-vus', vus: vus(20), duration: '30m' },
};
export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  scenarios: { catalog: profiles[mode] },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<750', 'p(99)<1500'],
    checks: ['rate>0.99'],
    unexpected_rate_limits: ['count==0'],
  },
};
const base = __ENV.BASE_URL || 'http://127.0.0.1:18081';
const limited = new Counter('unexpected_rate_limits');
export default function () {
  for (const path of ['/api/v1/projects?limit=20','/api/v1/users?limit=20','/api/v1/papers?limit=20']) {
    const response = http.get(base + path, {tags: {endpoint: path.split('?')[0]}});
    check(response, {'catalog success': r => r.status === 200, 'bounded page': r => { try {return Array.isArray(r.json('items')) && r.json('items').length <= 20;} catch (_) {return false;}}});
    limited.add(response.status === 429 ? 1 : 0);
    sleep(0.7 + Math.random() * 0.8);
  }
}
