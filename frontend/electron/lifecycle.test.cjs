const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const {
  resolvePythonExecutable,
  isVoiceVaultHealth,
  electronNodeEnvironment,
  waitForReady,
  isOwnedProcessRunning,
  terminateOwnedProcessTree,
} = require('./lifecycle.cjs')
const { EventEmitter } = require('node:events')

test('Python selection uses the project venv by default and honors an explicit override', () => {
  const root = path.resolve('project')
  assert.equal(resolvePythonExecutable(root, {}, value => value.endsWith('venv\\Scripts\\python.exe')),
    path.join(root, 'venv', 'Scripts', 'python.exe'))
  assert.equal(resolvePythonExecutable(root, { VOICEVAULT_PYTHON: 'D:\\tools\\python.exe' }, value => value.startsWith('D:')),
    'D:\\tools\\python.exe')
  assert.throws(() => resolvePythonExecutable(root, {}, () => false), /Run setup\.bat/)
})

test('health identity accepts only the VoiceVault API response', () => {
  assert.equal(isVoiceVaultHealth({ status: 'ok', service: 'voicevault-api', version: require('../package.json').version }), true)
  assert.equal(isVoiceVaultHealth({ status: 'ok', service: 'other-api', version: '0.1.1' }), false)
  assert.equal(isVoiceVaultHealth({ status: 'ok', service: 'voicevault-api', version: 'other' }), false)
})

test('Electron launches Node based tools with Electron run-as-Node mode enabled', () => {
  const env = electronNodeEnvironment({ PATH: 'existing-path', ELECTRON_RUN_AS_NODE: '0' })
  assert.deepEqual(env, { PATH: 'existing-path', ELECTRON_RUN_AS_NODE: '1' })
})

test('readiness polling returns a successful response and reports timeout context', async () => {
  const ready = await waitForReady('http://local/health', {
    timeoutMs: 100,
    intervalMs: 1,
    fetchImpl: async () => ({ ok: true, status: 200 }),
  })
  assert.equal(ready.status, 200)
  await assert.rejects(waitForReady('http://local/health', {
    timeoutMs: 3,
    intervalMs: 1,
    fetchImpl: async () => { throw new Error('offline') },
  }), /http:\/\/local\/health.*offline/)
})

test('Windows shutdown uses taskkill only for an owned live PID and waits for its result', async () => {
  const child = { pid: 4210, exitCode: null, signalCode: null, kill() { this.wasKilled = true } }
  let call
  const result = await terminateOwnedProcessTree(child, true, {
    platform: 'win32',
    env: { SystemRoot: 'C:\\Windows' },
    spawnImpl: (exe, args, options) => {
      call = { exe, args, options }
      const proc = new EventEmitter()
      queueMicrotask(() => proc.emit('close', 0))
      return proc
    },
  })
  assert.equal(result, true)
  assert.deepEqual(call, {
    exe: 'C:\\Windows\\System32\\taskkill.exe',
    args: ['/PID', '4210', '/T', '/F'],
    options: { windowsHide: true, stdio: 'ignore' },
  })
  assert.equal(child.wasKilled, undefined)
})

test('shutdown skips unowned or exited children and falls back when taskkill fails', async () => {
  const child = { pid: 4211, exitCode: null, signalCode: null, kill() { this.wasKilled = true } }
  let runs = 0
  assert.equal(isOwnedProcessRunning(child, false), false)
  assert.equal(await terminateOwnedProcessTree(child, false, { platform: 'win32', spawnImpl: () => { runs++ } }), false)
  assert.equal(runs, 0)
  assert.equal(await terminateOwnedProcessTree({ ...child, exitCode: 0 }, true, { platform: 'win32', spawnImpl: () => { runs++ } }), false)
  assert.equal(runs, 0)

  const failed = new EventEmitter()
  const result = await terminateOwnedProcessTree(child, true, {
    platform: 'win32',
    env: { SystemRoot: 'C:\\Windows' },
    spawnImpl: () => { runs++; queueMicrotask(() => failed.emit('close', 1)); return failed },
  })
  assert.equal(result, false)
  assert.equal(child.wasKilled, true)
  assert.equal(runs, 1)
})

test('actual backend and Vite startup reuse live children and respawn signaled children', async () => {
  const mainSource = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')
  const startupSource = mainSource.slice(mainSource.indexOf('async function startBackend()'), mainSource.indexOf('function startupHtml()'))
  // Execute the production functions with mocked process/network dependencies.
  for (const startup of ['startBackend', 'startVite']) {
    for (const signalCode of [null, 'SIGTERM']) {
      let spawns = 0
      const child = { pid: 4997, exitCode: null, signalCode }
      const harness = new Function('child', 'isOwnedProcessRunning', 'spawn', 'path', `
        let backendProcess=child, viteProcess=child, ownsBackend=true, ownsVite=true,closing=false;
        const backendUrl='http://fixture', frontendUrl='http://fixture', projectRoot='fixture', backendRoot='fixture', frontendRoot='fixture', runtimeEnv={}, servicePort=7763;
        const log=()=>{}, waitForReady=async()=>{}, waitForChild=async()=>{}, checkPort=async()=>false, isVoiceVaultBackend=async()=>true;
        const resolvePythonExecutable=()=>'fixture-python', electronNodeEnvironment=()=>({}), fs={existsSync:()=>true};
        ${startupSource}
        return {startBackend,startVite};
      `)(child, isOwnedProcessRunning, () => { spawns++; return { pid:4999, exitCode:null, signalCode:null } }, path)
      await harness[startup]()
      assert.equal(spawns, signalCode ? 1 : 0, `${startup}: ${signalCode || 'live'}`)
    }
  }
})

test('Windows taskkill has a bounded wait and falls back to the tracked child', async () => {
  const child = { pid: 4212, exitCode: null, signalCode: null, kill() { this.wasKilled = true } }
  const killer = new EventEmitter()
  killer.kill = () => { killer.wasKilled = true }
  const result = await terminateOwnedProcessTree(child, true, {
    platform: 'win32',
    env: { SystemRoot: 'C:\\Windows' },
    timeoutMs: 5,
    spawnImpl: () => killer,
  })
  assert.equal(result, false)
  assert.equal(killer.wasKilled, true)
  assert.equal(child.wasKilled, true)
})

test('actual packaged boot uses the bundled backend, runs first-run setup and loads built renderer', async () => {
  const { resolveDesktopLayout } = require('./packaging.cjs')
  const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')
  const bootSource = source.slice(source.indexOf('async function boot()'), source.indexOf('async function stopOwnedProcesses()'))
  const resources = path.resolve(__dirname, '../../fixture-install/resources')
  const project = path.resolve(__dirname, '../../fixture-runtime')
  const appDir = path.resolve(__dirname, '../../fixture.asar/electron')
  // external: VOICEVAULT_PROJECT_ROOT; bundled: installed resources; broken: resources missing.
  for (const mode of ['external', 'bundled', 'broken']) {
    const stages = []
    const env = mode === 'external' ? { VOICEVAULT_PROJECT_ROOT: project } : { LOCALAPPDATA: path.resolve(__dirname, '../../fixture-local') }
    const existsSync = candidate => mode !== 'broken' && (candidate === path.join(project, 'backend', 'main.py')
      || candidate === path.join(resources, 'voicevault', 'backend', 'main.py'))
    const harness = new Function('resolveDesktopLayout', 'path', 'app', '__dirname', 'stages', 'process', `
      let projectRoot, backendRoot, runtimeEnv, desktopLayout, booting=false, closing=false,startupStateError;
      const readRuntimeConfiguration=()=>({}), effectiveRuntimeEnvironment=()=>(${JSON.stringify(env)});
      const bootstrap={environmentLayout:()=>({logDir:'fixture-logs'})};
      const isDev=false, frontendRoot=path.resolve(__dirname,'..');
      const mainWindow={isDestroyed:()=>false,loadFile:async file=>stages.push(['load',file])};
      const showStartup=async(message,error)=>stages.push(['status',message,error]);
      const prepareBundledRuntime=async(layout,env)=>{stages.push(['setup',layout.stateDir]);return {...env,VOICEVAULT_PYTHON:'fixture-python'}};
      const startBackend=async()=>stages.push(['backend',backendRoot,runtimeEnv.VOICEVAULT_PYTHON]),startVite=async()=>{throw Error('unexpected Vite')}, log=()=>{};
      ${bootSource}
      return boot;
    `)(options => resolveDesktopLayout({ ...options, existsSync }), path,
      { isPackaged: true, getPath: () => 'fixture-user-data' }, appDir, stages, { resourcesPath: resources })
    await harness()
    if (mode === 'broken') {
      assert.equal(stages.some(stage => stage[0] === 'backend' || stage[0] === 'load'), false)
      assert.match(stages.at(-1)[2], /incomplete.*Reinstall/)
      assert.equal(stages.at(-1)[1], 'VoiceVault could not start.')
      continue
    }
    const root = mode === 'external' ? project : path.join(resources, 'voicevault')
    assert.deepEqual(stages.find(stage => stage[0] === 'backend').slice(0, 2), ['backend', path.join(root, 'backend')])
    assert.deepEqual(stages.find(stage => stage[0] === 'load'), ['load', path.resolve(appDir, '../dist/index.html')])
    if (mode === 'bundled') {
      assert.deepEqual(stages.find(stage => stage[0] === 'setup'), ['setup', path.join(env.LOCALAPPDATA, 'VoiceVault')])
      assert.equal(stages.find(stage => stage[0] === 'backend')[2], 'fixture-python')
    } else {
      assert.equal(stages.some(stage => stage[0] === 'setup'), false)
    }
  }
})

test('runtime configuration IPC accepts only the main startup frame and never changes running services', async () => {
  const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')
  const handlerSource = source.slice(source.indexOf('function isStartupSender('), source.indexOf('if (gotLock) {'))
  const calls = []
  const frame = {}
  const contents = { mainFrame: frame, getURL: () => 'data:startup' }
  const event = { sender: contents, senderFrame: frame }
  const harness = new Function('calls', 'contents', 'isOwnedProcessRunning', `
    let mainWindow={isDestroyed:()=>false,webContents:contents},startupUrl='data:startup',isDev=false,booting=false,closing=false,backendProcess,ownsBackend=false;
    const app={getPath:()=>'fixture-user-data'};
    const validateRuntimeConfiguration=input=>{calls.push(['validate',input]);if(input.invalid)throw Error('Invalid path');return input};
    const saveRuntimeConfiguration=(dir,input)=>calls.push(['save',dir,input]), boot=async()=>calls.push(['boot']);
    ${handlerSource}
    return {configureRuntime,setBusy:value=>booting=value,setRunning:()=>{backendProcess={pid:42,exitCode:null,signalCode:null};ownsBackend=true},navigate:()=>{contents.getURL=()=> 'file:renderer'}};
  `)(calls, contents, isOwnedProcessRunning)
  assert.match((await harness.configureRuntime({ sender: contents, senderFrame: {} }, {})).error, /startup recovery/)
  assert.equal(calls.length, 0)
  assert.match((await harness.configureRuntime(event, { invalid: true })).error, /Invalid path/)
  assert.equal(calls.filter(call => call[0] === 'save').length, 0)
  calls.length = 0
  assert.deepEqual(await harness.configureRuntime(event, { projectRoot: 'fixture' }), { saved: true })
  assert.deepEqual(calls.map(call => call[0]), ['validate', 'save', 'boot'])
  calls.length = 0
  harness.setBusy(true)
  assert.match((await harness.configureRuntime(event, {})).error, /Wait for startup/)
  harness.setBusy(false)
  harness.setRunning()
  assert.match((await harness.configureRuntime(event, {})).error, /backend is still running/)
  harness.navigate()
  assert.match((await harness.configureRuntime(event, {})).error, /startup recovery/)
  assert.equal(calls.length, 0)
})

test('closing during readiness preparation cannot spawn a service after shutdown', async () => {
  const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')
  const startups = source.slice(source.indexOf('async function startBackend()'), source.indexOf('function startupHtml()'))
  for (const name of ['startBackend', 'startVite']) {
    let resolvePort
    const port = new Promise(resolve => { resolvePort = resolve })
    let spawns = 0
    const harness = new Function('port', 'spawn', 'path', `
      let closing=false,backendProcess,viteProcess,ownsBackend=false,ownsVite=false;
      const isOwnedProcessRunning=()=>false,checkPort=()=>port;
      const backendUrl='',frontendUrl='',frontendRoot='',projectRoot='',backendRoot='',runtimeEnv={},servicePort=7763;
      const log=()=>{},resolvePythonExecutable=()=>'',waitForChild=async()=>{},isVoiceVaultBackend=async()=>true,waitForReady=async()=>{},electronNodeEnvironment=()=>({}),fs={existsSync:()=>true};
      ${startups}
      return {startBackend,startVite,close:()=>closing=true};
    `)(port, () => { spawns++; return {} }, path)
    const running = harness[name]()
    harness.close()
    resolvePort(false)
    await running
    assert.equal(spawns, 0, name)
  }
  const bootSource = source.slice(source.indexOf('async function boot()'), source.indexOf('async function stopOwnedProcesses()'))
  let resolveStatus
  const status = new Promise(resolve => { resolveStatus = resolve })
  const stages = []
  const harness = new Function('status', 'stages', `
    let booting=false,closing=false,projectRoot,backendRoot,runtimeEnv;
    const showStartup=()=>status,startBackend=async()=>stages.push('backend'),log=()=>{};
    const isDev=true,app={isPackaged:false},__dirname='',path={join:()=>''};
    const resolveDesktopLayout=()=>{stages.push('root');return {projectRoot:''}};
    ${bootSource}
    return {boot,close:()=>closing=true};
  `)(status, stages)
  const running = harness.boot()
  harness.close()
  resolveStatus()
  await running
  assert.deepEqual(stages, [])
})
