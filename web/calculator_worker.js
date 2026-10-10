let runtime;
let queue = Promise.resolve();
const runtimeURL = 'https://cdn.jsdelivr.net/pyodide/v0.29.5/full/';

async function initialize(bundle) {
  postMessage({type:'status', message:'正在準備計算…'});
  importScripts(runtimeURL+'pyodide.js');
  runtime = await loadPyodide({indexURL:runtimeURL, stdout:()=>{}, stderr:()=>{}});
  for (const [name, source] of Object.entries(bundle.modules)) {
    runtime.FS.writeFile('/home/pyodide/'+name, source);
  }
  runtime.globals.set('_market_json', JSON.stringify(bundle.data));
  await runtime.runPythonAsync('import json\nfrom web_calculator import run_comparison\n_market_data = json.loads(_market_json)\ndel _market_json');
  postMessage({type:'ready'});
}

onmessage = event => {
  const message = event.data;
  queue = queue.then(async () => {
    try {
      if (message.type === 'init') {
        await initialize(message.bundle);
      } else if (message.type === 'calculate') {
        runtime.globals.set('_request_json', JSON.stringify(message.params));
        const result = await runtime.runPythonAsync('json.dumps(run_comparison(_market_data, json.loads(_request_json)), ensure_ascii=False, allow_nan=False)');
        postMessage({type:'result', id:message.id, result:JSON.parse(result)});
      }
    } catch (error) {
      const lines = String(error.message || error).trim().split('\n');
      const detail = lines[lines.length-1];
      postMessage({type:'error', id:message.id, init:message.type==='init', message:detail});
    }
  });
};
