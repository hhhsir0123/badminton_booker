/* 配置页逻辑测试：YAML 解析/生成、默认值与校验、页面与脚本的一致性。
 * 运行：node test_files/test_config_io.mjs */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const IO = require('../src/web/config-io.js');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);
const read = (name) => readFileSync(path.join(ROOT, name), 'utf8');

let passed = 0;
function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`PASS  ${name}`);
  } catch (error) {
    console.log(`FAIL  ${name}\n      ${error.message}`);
    process.exitCode = 1;
  }
}

// ---------------------------------------------------------------- 解析
check('解析 config.example.yaml', () => {
  const parsed = IO.parse(read('config.example.yaml'));
  assert.equal(parsed.booking.target_time_in_day, '07:00:00');
  assert.equal(parsed.booking.booking_days_ahead, 2);
  assert.equal(parsed.booking.preferences.length, 2);
  assert.deepEqual(parsed.booking.preferences[0], { court: '任意', start: '19:00', end: '21:00' });
  assert.deepEqual(parsed.polling.polling_date_offsets, [0, 1, 2]);
  assert.deepEqual(parsed.polling.polling_time_slots, ['19:00-20:00']);
  assert.equal(parsed.user.password, '');
  assert.equal(parsed.login.auto_captcha, true);
  assert.equal(parsed.runtime.release_probe_interval_ms, 200);
});

check('解析注释与引号', () => {
  const parsed = IO.parse(
    ['# 顶部注释', 'user:', '  username: "13800000000"   # 手机号', "  partner_name: '张三'", '  email: a@b.c'].join('\n')
  );
  assert.equal(parsed.user.username, '13800000000');
  assert.equal(parsed.user.partner_name, '张三');
  assert.equal(parsed.user.email, 'a@b.c');
});

check('解析流式数组与映射', () => {
  const parsed = IO.parse('a: [1, 2, "x"]\nb: {c: 1, d: "y", e: true}\n');
  assert.deepEqual(parsed.a, [1, 2, 'x']);
  assert.deepEqual(parsed.b, { c: 1, d: 'y', e: true });
});

check('解析空值', () => {
  const parsed = IO.parse('a:\nb: ~\nc: ""\n');
  assert.equal(parsed.a, null);
  assert.equal(parsed.b, null);
  assert.equal(parsed.c, '');
});

check('Tab 缩进报错', () => {
  assert.throws(() => IO.parse('a:\n\tb: 1\n'), /Tab/);
});

check('缩进不一致报错', () => {
  assert.throws(() => IO.parse('a:\n  b: 1\n   c: 2\n'), /缩进/);
});

// ---------------------------------------------------------------- 往返
check('默认值校验通过', () => {
  assert.deepEqual(IO.validate(IO.defaults()), []);
});

check('归一化 + 生成 + 重新解析 往返一致', () => {
  const normalized = IO.normalize(IO.parse(read('config.example.yaml')));
  assert.deepEqual(IO.validate(normalized), []);
  const again = IO.normalize(IO.parse(IO.emit(normalized)));
  assert.deepEqual(again, normalized);
});

check('空轮询时段也能生成合法 YAML', () => {
  const config = IO.defaults();
  config.polling.polling_time_slots = [];
  const parsed = IO.parse(IO.emit(config));
  assert.deepEqual(parsed.polling.polling_time_slots, []);
});

check('旧版 preferred_time_slots 会转成候选', () => {
  const normalized = IO.normalize(
    IO.parse('booking:\n  preferred_time_slots:\n    - "18:00-20:00"\n')
  );
  assert.deepEqual(normalized.booking.preferences, [{ court: '任意', start: '18:00', end: '20:00' }]);
});

check('飞书配置往返一致', () => {
  const config = IO.defaults();
  config.feishu.webhook = 'https://open.feishu.cn/open-apis/bot/v2/hook/xyz';
  config.feishu.secret = 'abc';
  config.feishu.mention_all = true;
  config.feishu.notify_failure = true;
  const again = IO.parse(IO.emit(config));
  assert.equal(again.feishu.webhook, config.feishu.webhook);
  assert.equal(again.feishu.secret, 'abc');
  assert.equal(again.feishu.mention_all, true);
  assert.equal(again.feishu.notify_failure, true);
});

check('飞书 webhook 格式校验', () => {
  const config = IO.defaults();
  config.feishu.webhook = 'open.feishu.cn/xxx';
  assert.ok(IO.validate(config).some((item) => item.includes('webhook')));
});

// ---------------------------------------------------------------- 校验
check('校验：时间格式', () => {
  const config = IO.defaults();
  config.booking.target_time_in_day = '7:00';
  assert.ok(IO.validate(config).some((item) => item.includes('HH:MM:SS')));
});

check('校验：时段顺序', () => {
  const config = IO.defaults();
  config.booking.preferences = [{ court: '任意', start: '21:00', end: '19:00' }];
  assert.ok(IO.validate(config).some((item) => item.includes('晚于')));
});

check('校验：没有候选', () => {
  const config = IO.defaults();
  config.booking.preferences = [];
  assert.ok(IO.validate(config).some((item) => item.includes('至少')));
});

check('校验：偏移不是数字', () => {
  const config = IO.defaults();
  config.polling.polling_date_offsets = [Number.NaN];
  assert.ok(IO.validate(config).some((item) => item.includes('偏移')));
});

// ---------------------------------------------------------------- 页面一致性
check('页面脚本引用的元素都存在', () => {
  const html = read('src/web/index.html');
  const app = read('src/web/app.js');
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]));
  const used = new Set([...app.matchAll(/\$\('#([^']+)'\)/g)].map((match) => match[1]));
  const missing = [...used].filter((id) => !ids.has(id));
  assert.deepEqual(missing, []);
});

check('config-io.js 在 app.js 之前加载', () => {
  const html = read('src/web/index.html');
  assert.ok(html.indexOf('config-io.js') > 0);
  assert.ok(html.indexOf('config-io.js') < html.indexOf('app.js'));
});

console.log(`\n${passed} 项通过${process.exitCode ? '，有失败' : ''}`);
