/* 配置文件的解析 / 校验 / 生成。
 *
 * 为什么需要它：纯静态页面拿不到文件系统，也不能 fetch 本地 config.yaml（file:// 会被
 * CORS 拦掉）。所以页面用 <input type="file"> 把 config.yaml 读成文本，这里负责解析，
 * 编辑完再生成 YAML 让用户下载覆盖。
 *
 * 只支持本项目配置用到的那部分 YAML：缩进映射、块序列、流式 [] / {}、引号、注释。
 * 完整 YAML（锚点、多文档、多行标量）不在范围内，遇到会抛错而不是猜。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.ConfigIO = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ---------------------------------------------------------------- 解析

  function stripComment(line) {
    let quote = null;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (quote) {
        if (ch === '\\' && quote === '"') {
          i += 1;
        } else if (ch === quote) {
          quote = null;
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === '#' && (i === 0 || /\s/.test(line[i - 1]))) {
        return line.slice(0, i);
      }
    }
    return line;
  }

  function tokenize(text) {
    const lines = [];
    String(text).replace(/\r\n?/g, '\n').split('\n').forEach(function (raw, index) {
      const leading = raw.slice(0, raw.length - raw.trimStart().length);
      if (leading.indexOf('\t') >= 0) {
        throw new Error('第 ' + (index + 1) + ' 行用了 Tab 缩进，请改成空格');
      }
      const body = stripComment(raw);
      if (!body.trim()) {
        return;
      }
      lines.push({
        indent: body.length - body.trimStart().length,
        content: body.trim(),
        line: index + 1
      });
    });
    return lines;
  }

  function splitEntry(content) {
    let quote = null;
    let depth = 0;
    for (let i = 0; i < content.length; i += 1) {
      const ch = content[i];
      if (quote) {
        if (ch === '\\' && quote === '"') {
          i += 1;
        } else if (ch === quote) {
          quote = null;
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === '[' || ch === '{') {
        depth += 1;
      } else if (ch === ']' || ch === '}') {
        depth -= 1;
      } else if (ch === ':' && depth === 0) {
        const next = content[i + 1];
        if (next === undefined || next === ' ') {
          return [content.slice(0, i).trim(), content.slice(i + 1).trim()];
        }
      }
    }
    return null;
  }

  function splitFlow(inner) {
    const parts = [];
    let quote = null;
    let depth = 0;
    let start = 0;
    for (let i = 0; i < inner.length; i += 1) {
      const ch = inner[i];
      if (quote) {
        if (ch === '\\' && quote === '"') {
          i += 1;
        } else if (ch === quote) {
          quote = null;
        }
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === '[' || ch === '{') {
        depth += 1;
      } else if (ch === ']' || ch === '}') {
        depth -= 1;
      } else if (ch === ',' && depth === 0) {
        parts.push(inner.slice(start, i));
        start = i + 1;
      }
    }
    parts.push(inner.slice(start));
    return parts.map(function (part) { return part.trim(); }).filter(function (part) { return part.length; });
  }

  function unquote(text) {
    const quote = text[0];
    const inner = text.slice(1, -1);
    if (quote === "'") {
      return inner.replace(/''/g, "'");
    }
    return inner.replace(/\\(["\\/bfnrt])/g, function (_, code) {
      return { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' }[code];
    });
  }

  function parseScalar(value) {
    const text = String(value).trim();
    if (!text) {
      return null;
    }
    if (text === '~' || text === 'null' || text === 'Null' || text === 'NULL') {
      return null;
    }
    if (text === 'true' || text === 'false') {
      return text === 'true';
    }
    if (/^-?\d+$/.test(text)) {
      return Number(text);
    }
    if (/^-?\d+\.\d+$/.test(text)) {
      return Number(text);
    }
    if ((text[0] === '"' && text[text.length - 1] === '"') || (text[0] === "'" && text[text.length - 1] === "'")) {
      return unquote(text);
    }
    if (text[0] === '[' && text[text.length - 1] === ']') {
      return splitFlow(text.slice(1, -1)).map(parseScalar);
    }
    if (text[0] === '{' && text[text.length - 1] === '}') {
      const map = {};
      splitFlow(text.slice(1, -1)).forEach(function (part) {
        const entry = splitEntry(part);
        if (!entry) {
          throw new Error('无法解析流式映射片段: ' + part);
        }
        map[parseScalar(entry[0])] = parseScalar(entry[1]);
      });
      return map;
    }
    return text;
  }

  function parse(text) {
    const lines = tokenize(text);
    let index = 0;

    function fail(message) {
      const where = lines[index] ? '（第 ' + lines[index].line + ' 行）' : '（文件末尾）';
      throw new Error(message + where);
    }

    function parseMap(indent) {
      const map = {};
      while (index < lines.length) {
        const line = lines[index];
        if (line.indent < indent) {
          break;
        }
        if (line.indent > indent) {
          fail('缩进不一致');
        }
        if (line.content[0] === '-') {
          break;
        }
        const entry = splitEntry(line.content);
        if (!entry) {
          fail('缺少冒号: ' + line.content);
        }
        const key = parseScalar(entry[0]);
        index += 1;
        if (entry[1] !== '') {
          map[key] = parseScalar(entry[1]);
        } else if (index < lines.length && lines[index].indent > indent) {
          map[key] = parseNode(lines[index].indent);
        } else {
          map[key] = null;
        }
      }
      return map;
    }

    function parseSeq(indent) {
      const list = [];
      while (index < lines.length) {
        const line = lines[index];
        if (line.indent !== indent || line.content[0] !== '-') {
          break;
        }
        const rest = line.content.slice(1).trim();
        if (!rest) {
          index += 1;
          list.push(index < lines.length && lines[index].indent > indent ? parseNode(lines[index].indent) : null);
          continue;
        }
        const entry = splitEntry(rest);
        if (!entry) {
          list.push(parseScalar(rest));
          index += 1;
          continue;
        }
        // "- key: value" 相当于一个缩进更深的映射
        const mapIndent = indent + 2;
        const item = {};
        const key = parseScalar(entry[0]);
        index += 1;
        if (entry[1] !== '') {
          item[key] = parseScalar(entry[1]);
        } else if (index < lines.length && lines[index].indent > mapIndent) {
          item[key] = parseNode(lines[index].indent);
        } else {
          item[key] = null;
        }
        while (index < lines.length && lines[index].indent >= mapIndent) {
          if (lines[index].indent !== mapIndent) {
            fail('列表项的缩进不一致');
          }
          if (lines[index].content[0] === '-') {
            break;
          }
          const pair = splitEntry(lines[index].content);
          if (!pair) {
            fail('缺少冒号: ' + lines[index].content);
          }
          const subKey = parseScalar(pair[0]);
          index += 1;
          if (pair[1] !== '') {
            item[subKey] = parseScalar(pair[1]);
          } else if (index < lines.length && lines[index].indent > mapIndent) {
            item[subKey] = parseNode(lines[index].indent);
          } else {
            item[subKey] = null;
          }
        }
        list.push(item);
      }
      return list;
    }

    function parseNode(indent) {
      if (lines[index].content[0] === '-') {
        return parseSeq(indent);
      }
      return parseMap(indent);
    }

    if (!lines.length) {
      return {};
    }
    if (lines[0].content[0] === '-') {
      throw new Error('配置文件顶层必须是映射（key: value）');
    }
    const result = parseMap(lines[0].indent);
    if (index < lines.length) {
      fail('多余的缩进或无法解析的内容');
    }
    return result;
  }

  // ---------------------------------------------------------------- 生成

  function quoteString(text) {
    const escaped = String(text).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    return '"' + escaped + '"';
  }

  function emitScalar(value) {
    if (value === null || value === undefined) {
      return '""';
    }
    if (typeof value === 'boolean') {
      return String(value);
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return String(value);
    }
    return quoteString(value);
  }

  const HEADER = [
    '# 羽毛球自动预约 · 配置',
    '# 由网页生成：直接覆盖项目根目录的 config.yaml 即可。',
    '# 字段含义见 config.example.yaml。',
    ''
  ].join('\n');

  function emit(config) {
    const user = config.user || {};
    const booking = config.booking || {};
    const login = config.login || {};
    const polling = config.polling || {};
    const email = config.email || {};
    const feishu = config.feishu || {};
    const runtime = config.runtime || {};
    const captcha = config.captcha || {};
    const lines = [HEADER];

    lines.push('user:');
    lines.push('  username: ' + emitScalar(user.username || ''));
    lines.push('  password: ' + emitScalar(user.password || ''));
    lines.push('  email: ' + emitScalar(user.email || ''));
    lines.push('  partner_name: ' + emitScalar(user.partner_name || ''));
    lines.push('');

    lines.push('booking:');
    lines.push('  venue_type: ' + emitScalar(booking.venue_type || '羽毛球'));
    lines.push('  target_time_in_day: ' + emitScalar(booking.target_time_in_day || '07:00:00'));
    lines.push('  booking_days_ahead: ' + emitScalar(Number(booking.booking_days_ahead || 0)));
    lines.push('  booking_date: ' + emitScalar(booking.booking_date || ''));
    lines.push('  advance_time: ' + emitScalar(Number(booking.advance_time || 0)));
    lines.push('  preferences:');
    (booking.preferences || []).forEach(function (item) {
      lines.push('    - court: ' + emitScalar(item.court || ''));
      lines.push('      start: ' + emitScalar(item.start || ''));
      lines.push('      end: ' + emitScalar(item.end || ''));
    });
    lines.push('  allow_partial: ' + emitScalar(Boolean(booking.allow_partial)));
    lines.push('  fallback_any: ' + emitScalar(Boolean(booking.fallback_any)));
    lines.push('  max_attempts: ' + emitScalar(Number(booking.max_attempts || 1)));
    lines.push('');

    lines.push('login:');
    lines.push('  url: ' + emitScalar(login.url || ''));
    lines.push('  headless: ' + emitScalar(Boolean(login.headless)));
    lines.push('  auto_captcha: ' + emitScalar(login.auto_captcha !== false));
    lines.push('  max_retry: ' + emitScalar(Number(login.max_retry || 1)));
    lines.push('  profile_dir: ' + emitScalar(login.profile_dir || '.chrome-profile'));
    lines.push('  session_check: ' + emitScalar(login.session_check !== false));
    lines.push('');

    lines.push('polling:');
    lines.push('  polling_interval: ' + emitScalar(Number(polling.polling_interval || 60)));
    const slots = polling.polling_time_slots || [];
    if (!slots.length) {
      lines.push('  polling_time_slots: []');
    } else {
      lines.push('  polling_time_slots:');
      slots.forEach(function (slot) {
        lines.push('    - ' + emitScalar(slot));
      });
    }
    lines.push('  polling_date_offsets: [' + (polling.polling_date_offsets || [0]).map(Number).join(', ') + ']');
    lines.push('  is_auto_booking: ' + emitScalar(Boolean(polling.is_auto_booking)));
    lines.push('');

    lines.push('email:');
    lines.push('  smtp_server: ' + emitScalar(email.smtp_server || ''));
    lines.push('  smtp_port: ' + emitScalar(Number(email.smtp_port || 465)));
    lines.push('  sender_email: ' + emitScalar(email.sender_email || ''));
    lines.push('  sender_password: ' + emitScalar(email.sender_password || ''));
    lines.push('  use_ssl: ' + emitScalar(email.use_ssl !== false));
    lines.push('');

    lines.push('feishu:');
    lines.push('  webhook: ' + emitScalar(feishu.webhook || ''));
    lines.push('  secret: ' + emitScalar(feishu.secret || ''));
    lines.push('  mention_all: ' + emitScalar(Boolean(feishu.mention_all)));
    lines.push('  notify_availability: ' + emitScalar(feishu.notify_availability !== false));
    lines.push('  notify_success: ' + emitScalar(feishu.notify_success !== false));
    lines.push('  notify_failure: ' + emitScalar(Boolean(feishu.notify_failure)));
    lines.push('');

    lines.push('runtime:');
    lines.push('  clock_offset_ms: ' + emitScalar(Number(runtime.clock_offset_ms || 0)));
    lines.push('  release_probe_lead_ms: ' + emitScalar(Number(runtime.release_probe_lead_ms || 1500)));
    lines.push('  release_probe_interval_ms: ' + emitScalar(Number(runtime.release_probe_interval_ms || 120)));
    lines.push('  release_grace_ms: ' + emitScalar(Number(runtime.release_grace_ms || 4000)));
    lines.push('  grid_refresh: ' + emitScalar(runtime.grid_refresh || 'auto'));
    lines.push('  submit_timeout_ms: ' + emitScalar(Number(runtime.submit_timeout_ms || 6000)));
    lines.push('  keep_open_after_success: ' + emitScalar(Number(runtime.keep_open_after_success || 600)));
    lines.push('');

    lines.push('captcha:');
    lines.push('  provider: ' + emitScalar(captcha.provider || 'local'));
    lines.push('  fallback_to_local: ' + emitScalar(captcha.fallback_to_local !== false));
    lines.push('  max_attempts: ' + emitScalar(Number(captcha.max_attempts || 1)));
    lines.push('  min_length: ' + emitScalar(Number(captcha.min_length || 4)));
    lines.push('  max_length: ' + emitScalar(Number(captcha.max_length || 6)));
    lines.push('  ensemble: ' + emitScalar(captcha.ensemble !== false));
    lines.push('  pre_solve: ' + emitScalar(captcha.pre_solve !== false));
    const tts = captcha.ttshitu || {};
    lines.push('  ttshitu:');
    lines.push('    username: ' + emitScalar(tts.username || ''));
    lines.push('    password: ' + emitScalar(tts.password || ''));
    lines.push('    type_id: ' + emitScalar(Number(tts.type_id || 43)));
    lines.push('    timeout_seconds: ' + emitScalar(Number(tts.timeout_seconds || 20)));
    lines.push('');

    return lines.join('\n');
  }

  // ---------------------------------------------------------------- 默认值

  function defaults() {
    return {
      user: { username: '', password: '', email: '', partner_name: '' },
      booking: {
        venue_type: '羽毛球',
        target_time_in_day: '07:00:00',
        booking_days_ahead: 2,
        booking_date: '',
        advance_time: 120,
        preferences: [
          { court: '任意', start: '19:00', end: '21:00' },
          { court: '训练馆3号', start: '19:00', end: '20:00' }
        ],
        allow_partial: false,
        fallback_any: false,
        max_attempts: 3
      },
      login: {
        url: 'https://ggtypt.cumtb.edu.cn/venue/login',
        headless: false,
        auto_captcha: true,
        max_retry: 2,
        profile_dir: '.chrome-profile',
        session_check: true
      },
      polling: {
        polling_interval: 60,
        polling_time_slots: ['19:00-20:00'],
        polling_date_offsets: [0, 1, 2],
        is_auto_booking: false
      },
      web: { host: '127.0.0.1', port: 8765, auto_open: true },
      email: { smtp_server: '', smtp_port: 465, sender_email: '', sender_password: '', use_ssl: true },
      feishu: {
        webhook: '',
        secret: '',
        mention_all: false,
        notify_availability: true,
        notify_success: true,
        notify_failure: false
      },
      runtime: {
        clock_offset_ms: 0,
        release_probe_lead_ms: 1500,
        release_probe_interval_ms: 200,
        release_grace_ms: 4000,
        grid_refresh: 'auto',
        submit_timeout_ms: 6000,
        keep_open_after_success: 600
      },
      captcha: {
        // local = 只用本地 OCR；ttshitu = 图鉴打码（type_id 43 传目标词，最准）
        provider: 'local',
        fallback_to_local: true,
        ttshitu: { username: '', password: '', type_id: 43, timeout_seconds: 20 },
        max_attempts: 1,
        min_length: 4,
        max_length: 6,
        ensemble: true,
        pre_solve: true
      }
    };
  }

  // ---------------------------------------------------------------- 校验

  const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
  const CLOCK_SECONDS = /^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/;

  function isInt(value) {
    return typeof value === 'number' && Number.isInteger(value);
  }

  function validate(config) {
    const errors = [];
    const booking = config.booking || {};
    const polling = config.polling || {};
    const login = config.login || {};

    if (!CLOCK_SECONDS.test(String(booking.target_time_in_day || ''))) {
      errors.push('开售时间需要 HH:MM:SS 格式，例如 07:00:00');
    }
    if (!isInt(booking.booking_days_ahead) || booking.booking_days_ahead < 0 || booking.booking_days_ahead > 30) {
      errors.push('提前天数应为 0~30 的整数');
    }
    if (!isInt(booking.advance_time) || booking.advance_time < 10 || booking.advance_time > 3600) {
      errors.push('提前打开时间应为 10~3600 秒');
    }
    if (booking.booking_date && !/^\d{4}-\d{2}-\d{2}$/.test(booking.booking_date)) {
      errors.push('指定入场日需要 YYYY-MM-DD 格式');
    }
    const prefs = booking.preferences || [];
    if (!prefs.length) {
      errors.push('至少需要一条场地/时段偏好');
    }
    prefs.forEach(function (item, index) {
      const label = '第 ' + (index + 1) + ' 条偏好';
      if (!item || typeof item.court !== 'string' || !item.court.trim()) {
        errors.push(label + '缺少场地');
      }
      if (!CLOCK.test(String(item.start || ''))) {
        errors.push(label + '的开始时间需要 HH:MM');
      }
      if (!CLOCK.test(String(item.end || ''))) {
        errors.push(label + '的结束时间需要 HH:MM');
      }
      if (CLOCK.test(String(item.start || '')) && CLOCK.test(String(item.end || '')) && item.start >= item.end) {
        errors.push(label + '的结束时间要晚于开始时间');
      }
    });
    if (!isInt(booking.max_attempts) || booking.max_attempts < 1 || booking.max_attempts > 10) {
      errors.push('最大提交次数应为 1~10');
    }
    if (typeof login.url !== 'string' || login.url.indexOf('http') !== 0) {
      errors.push('登录地址需要以 http(s):// 开头');
    }
    if (!isInt(polling.polling_interval) || polling.polling_interval < 5 || polling.polling_interval > 3600) {
      errors.push('轮询间隔应为 5~3600 秒');
    }
    const offsets = polling.polling_date_offsets || [];
    if (!offsets.length) {
      errors.push('轮询日期偏移需要至少一个数字');
    }
    offsets.forEach(function (offset) {
      if (!isInt(offset) || offset < 0 || offset > 30) {
        errors.push('轮询日期偏移应为 0~30 的整数');
      }
    });
    const feishu = config.feishu || {};
    if (feishu.webhook && String(feishu.webhook).indexOf('http') !== 0) {
      errors.push('飞书 webhook 需要以 https:// 开头（open-apis/bot/v2/hook/…）');
    }
    const captcha = config.captcha || {};
    const provider = String(captcha.provider || 'local');
    if (provider !== 'local' && provider !== 'ttshitu') {
      errors.push('验证码识别方式只能是 local 或 ttshitu');
    }
    if (provider === 'ttshitu') {
      const tts = captcha.ttshitu || {};
      if (!String(tts.username || '').trim()) {
        errors.push('选择图鉴打码时，需要填图鉴用户名');
      }
      if (!String(tts.password || '')) {
        errors.push('选择图鉴打码时，需要填图鉴密码');
      }
      if (!isInt(tts.type_id) || tts.type_id < 1 || tts.type_id > 99) {
        errors.push('图鉴题型应为 1~99 的整数（推荐 43）');
      }
    }
    return errors;
  }

  // 解析结果 → 表单需要的结构（补齐缺省字段，容忍手写配置里的简写）
  function normalize(parsed) {
    const base = defaults();
    const source = parsed && typeof parsed === 'object' ? parsed : {};
    const result = JSON.parse(JSON.stringify(base));

    Object.keys(source).forEach(function (section) {
      if (result[section] && typeof result[section] === 'object' && !Array.isArray(result[section])) {
        Object.keys(source[section] || {}).forEach(function (key) {
          if (result[section][key] !== undefined) {
            result[section][key] = source[section][key];
          }
        });
      }
    });

    // 图鉴账号做一层深合并，手写配置缺字段时补默认值
    const sourceTts = (source.captcha && source.captcha.ttshitu) || {};
    result.captcha.ttshitu = Object.assign({}, base.captcha.ttshitu, sourceTts);

    result.booking.preferences = (result.booking.preferences || [])
      .filter(function (item) { return item && typeof item === 'object'; })
      .map(function (item) {
        return {
          court: String(item.court === undefined || item.court === null ? '任意' : item.court),
          start: String(item.start || ''),
          end: String(item.end || '')
        };
      });
    if (!result.booking.preferences.length) {
      result.booking.preferences = [{ court: '任意', start: '19:00', end: '21:00' }];
    }

    result.polling.polling_time_slots = (result.polling.polling_time_slots || [])
      .map(String)
      .filter(function (item) { return item.trim().length; });
    result.polling.polling_date_offsets = (result.polling.polling_date_offsets || [0]).map(Number);

    // 兼容旧配置：只有 preferred_time_slots 时，转成 preferences
    if (!source.booking || (!source.booking.preferences && source.booking.preferred_time_slots)) {
      const legacy = source.booking && source.booking.preferred_time_slots;
      if (Array.isArray(legacy) && legacy.length) {
        result.booking.preferences = legacy.map(function (slot) {
          const match = /^(\d{1,2}:\d{2})\s*[-–—~]\s*(\d{1,2}:\d{2})$/.exec(String(slot).trim());
          return match
            ? { court: '任意', start: match[1], end: match[2] }
            : { court: '任意', start: '19:00', end: '21:00' };
        });
      }
    }
    return result;
  }

  return {
    parse: parse,
    emit: emit,
    defaults: defaults,
    validate: validate,
    normalize: normalize
  };
});
