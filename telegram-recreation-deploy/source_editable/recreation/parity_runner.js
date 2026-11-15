/* Trusted, frozen JS from the user's original n8n workflow. No n8n dependency.
   Never execute user-supplied scripts: only names from parity_assets.json.
*/
const fs = require('node:fs');
const path = require('node:path');
const assets = JSON.parse(fs.readFileSync(path.join(__dirname, 'parity_assets.json'), 'utf8'));
let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; if (raw.length > 5e6) {process.stderr.write('input too large'); process.exit(2);} });
process.stdin.on('end', () => {
  try {
    const data = JSON.parse(raw);
    const script = assets[data.script];
    if (!script) throw new Error('Unknown frozen workflow script');
    const named = data.named || {};
    const $ = name => ({
      first: () => ({json: named[name] || {}}),
      all: () => (Array.isArray(named[name]) ? named[name] : [named[name] || {}]).map(json => ({json}))
    });
    const $input = {first: () => ({json: data.input || {}}), all: () => [{json: data.input || {}}]};
    const $json = data.input || {};
    let answer;
    if (script.kind === 'code') {
      // These scripts are committed, static, reviewed source code, not remote strings.
      answer = new Function('$input', '$', '$json', script.source)($input, $, $json);
      if (!Array.isArray(answer) || !answer[0] || !answer[0].json) throw new Error('Bad Code node output');
      answer = answer[0].json;
    } else {
      let expr = script.source.trim();
      if (!expr.startsWith('={{') || !expr.endsWith('}}')) throw new Error('Unexpected n8n expression');
      expr = expr.slice(3,-2);
      answer = new Function('$input', '$', '$json', `return (${expr});`)($input, $, $json);
      if (typeof answer === 'string') { try {answer = JSON.parse(answer);} catch {} }
    }
    process.stdout.write(JSON.stringify({ok:true, result:answer}));
  } catch(e) {
    process.stdout.write(JSON.stringify({ok:false,error:String(e?.stack || e)}));
    process.exitCode=1;
  }
});
