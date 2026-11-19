/* 执行真实注入脚本，验证 XHR/fetch 脱敏监听与目标日期响应匹配。 */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = fileURLToPath(new URL('..', import.meta.url));
const source = JSON.parse(execFileSync('python3', ['-c',
  'import ast,json; t=ast.parse(open("src/core/page_js.py").read()); print(json.dumps({n.targets[0].id:ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and n.targets[0].id in ("HELPERS","CLICK_DATE_JS")}))'], {cwd: root, encoding: 'utf8'}));
const day = '2026-11-22';
const valid = {code: 200, data: {reservationDateSpaceInfo: {[day]: []}}};
const liveShape = {code: 200, data: {reservationDateSpaceInfo: {[day]: [{
  venueSpaceGroupId: null, spaceName: '训练馆3号', enSpaceName: null, id: 3, venueSiteId: 3,
  'time-id': {reservationStatus: 4, spaceName: '训练馆3号'}
}]}}};

async function scenario(payload, requestDay = day, transport = 'xhr') {
  class FakeXHR {
    open(method, url) { this.url = url; }
    addEventListener(name, callback) { this.callback = callback; }
    send() {
      this.status = 200;
      this.responseText = JSON.stringify(payload);
      this.callback();
    }
  }
  let context;
  const request = () => {
    const url = '/venue-server/api/reservation/day/info?searchDate=' + requestDay;
    if (transport === 'fetch') { context.window.fetch(url); }
    else { const xhr = new context.XMLHttpRequest(); xhr.open('GET', url); xhr.send(); }
  };
  context = vm.createContext({URL, URLSearchParams, XMLHttpRequest: FakeXHR, performance,
    setTimeout, getComputedStyle: () => ({}),
    location: {origin: 'https://example.test'},
    document: {querySelectorAll: () => [{getClientRects: () => [1], click: request}]},
    window: {fetch: async () => ({status: 200, clone: () => ({text: async () => JSON.stringify(payload)})})}});
  vm.runInContext(source.HELPERS, context);
  const click = vm.runInContext('(function(){' + source.CLICK_DATE_JS + '})', context);
  const result = await new Promise(resolve => click(0, 5, day, resolve));
  return {result, records: context.window.__bb.dayResponses};
}

for (const transport of ['xhr', 'fetch']) {
  assert.equal((await scenario(valid, day, transport)).result.ok, true);
  assert.equal((await scenario(liveShape, day, transport)).result.ok, true);
  const failure = await scenario({code: 401, message: 'private-secret'}, day, transport);
  assert.equal(failure.result.ok, false);
  assert.equal(failure.result.error, 'business-error');
  assert.equal(JSON.stringify(failure.records).includes('private-secret'), false);
  const wrongDay = await scenario(valid, '2026-11-21', transport);
  assert.equal(wrongDay.result.ok, false);
  assert.equal(wrongDay.result.error, 'no-response');
  const malformed = await scenario({code: 200, data: {reservationDateSpaceInfo: {[day]: [{x: null}]}}}, day, transport);
  assert.equal(malformed.result.ok, false);
}
console.log('PASS: actual XHR/fetch hooks validate target date, business result and malformed data without retaining secrets');
