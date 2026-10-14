const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const yaml = require('js-yaml');

const root = path.resolve(__dirname, '../..');
const workflow = name => yaml.load(fs.readFileSync(path.join(root, '.github/workflows', name), 'utf8'));

function prepare(namespace, repository, ref = 'refs/pull/1/merge', settings = {}, purpose = 'deployment') {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ci-repository-'));
  const output = path.join(directory, 'output');
  const environment = path.join(directory, 'environment');
  const read = file => fs.existsSync(file) ? Object.fromEntries(fs.readFileSync(file, 'utf8').trim().split('\n').map(line => line.split('='))) : {};
  try {
    const log = execFileSync('python3', ['devops/scripts/ci_repository_scope.py', 'prepare', '--purpose', purpose], {
      cwd: root, encoding: 'utf8',
      env: { ...process.env, CI_NAMESPACE: namespace, GITHUB_REPOSITORY_ID: repository,
        CI_GIT_REF: ref, CI_LOCATION: 'switzerlandnorth', CI_AZURE_ENVIRONMENT: 'AzureCloud',
        CI_DEPLOYMENTS_ENABLED: '', CI_CLEANUP_ENABLED: '', CI_MAIN_ENABLED: '',
        GITHUB_OUTPUT: output, GITHUB_ENV: environment, ...settings }
    });
    return { names: read(environment), outputs: read(output), log };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

const enabled = { CI_DEPLOYMENTS_ENABLED: 'true', CI_MAIN_ENABLED: 'true' };

test('legacy names remain unchanged; CGI identities separate repositories and main', () => {
  const legacy = prepare('', '1');
  expect(legacy.names).toEqual({});
  expect(legacy.outputs.deployment).toBe('allowed');
  expect(legacy.outputs.refid).toMatch(/^[0-9a-f]{8}$/);
  const first = prepare('cgi', '1', undefined, enabled).names;
  const other = prepare('cgi', '2', undefined, enabled).names;
  const main = prepare('cgi', '1', 'refs/heads/main', enabled).names;
  for (const key of Object.keys(first)) {
    expect(first[key]).not.toBe(other[key]);
    expect(first[key]).not.toBe(main[key]);
  }
  expect(first.CI_TRE_ID).toMatch(/^cgi[0-9a-f]{8}$/);
  expect(first.CI_MGMT_STORAGE_ACCOUNT_NAME.length).toBeLessThanOrEqual(24);
  expect(first.CI_ENCRYPTION_KV_NAME.length).toBeLessThanOrEqual(24);
});

test('names are masked locally and never passed through namespaced job outputs', () => {
  const { names, outputs, log } = prepare('cgi', '1', undefined, enabled);
  expect(outputs).toEqual({ deployment: 'allowed' });
  for (const value of Object.values(names)) {
    expect(log).toContain(`::add-mask::${value}\n`);
    expect(log).toContain(`::add-mask::${value.toUpperCase()}\n`);
  }
  expect(log.split('\n').filter(Boolean).every(line => line.startsWith('::add-mask::'))).toBe(true);
  // Model literal runner masks. A live Actions check is still required.
  const masks = log.trim().split('\n').map(line => line.slice('::add-mask::'.length));
  const raw = `TRE_ID=${names.CI_TRE_ID} rg-${names.CI_TRE_ID}-ws-synthetic https://${names.CI_TRE_ID}.example`;
  const redacted = masks.reduce((text, mask) => text.split(mask).join('***'), raw);
  expect(redacted).not.toContain(names.CI_ENVIRONMENT_ID);
  expect(redacted).not.toContain('cgi');
});

test('deployment requires the secret switch and main requires a separate switch', () => {
  for (const flag of ['', 'false', 'yes', 'TRUE']) {
    const result = prepare('cgi', '1', undefined, { CI_DEPLOYMENTS_ENABLED: flag });
    expect(result.outputs).toEqual({ deployment: 'blocked' });
    expect(result.names).toEqual({});
  }
  expect(prepare('cgi', '1', undefined, { CI_DEPLOYMENTS_ENABLED: 'true' }).outputs.deployment).toBe('allowed');
  expect(prepare('cgi', '1', 'refs/heads/main', { CI_DEPLOYMENTS_ENABLED: 'true' }).outputs.deployment).toBe('blocked');
  expect(prepare('cgi', '1', 'refs/heads/main', enabled).outputs.deployment).toBe('allowed');
});

test('cleanup has its own switch and legacy disposable validation stays unavailable for CGI', () => {
  expect(prepare('cgi', '1', undefined, enabled, 'cleanup').outputs).toEqual({ cleanup: 'blocked' });
  expect(prepare('cgi', '1', undefined, { CI_CLEANUP_ENABLED: 'true' }, 'cleanup').outputs).toEqual({ cleanup: 'allowed' });
  expect(prepare('cgi', '1', undefined, { CI_CLEANUP_ENABLED: 'true' }, 'validation').outputs).toEqual({ validation: 'blocked' });
  expect(prepare('', '1', undefined, {}, 'validation').outputs).toEqual({ validation: 'allowed' });
});

test('every reusable consumer resolves and masks names before consuming them', () => {
  const jobs = workflow('deploy_tre_reusable.yml').jobs;
  expect(jobs.ci_config.outputs).toEqual({ deployment: '${{ steps.scope.outputs.deployment }}' });
  for (const [name, job] of Object.entries(jobs)) {
    let masked = false;
    for (const step of job.steps || []) {
      if (step.run === 'python3 devops/scripts/ci_repository_scope.py resolve') masked = true;
      if (JSON.stringify(step).includes('env.CI_TRE_ID')) expect({ name, masked }).toEqual({ name, masked: true });
      if (step.uses !== './.github/actions/devcontainer_run_command') continue;
      for (const key of ['TRE_ID', 'ACR_NAME', 'MGMT_RESOURCE_GROUP_NAME', 'MGMT_STORAGE_ACCOUNT_NAME', 'CI_CACHE_ACR_NAME']) {
        expect(step.with[key]).toContain('env.CI_');
        expect(step.with[key]).toContain("env.CI_NAMESPACE == '' && secrets.");
      }
      expect(step.with.CI_GIT_REF).toBe('${{ inputs.ciGitRef }}');
    }
    expect(JSON.stringify(job)).not.toMatch(/needs\.ci_config\.outputs\.(?!deployment)/);
  }
  for (const name of ['deploy_management', 'e2e_tests_smoke', 'e2e_tests_custom', 'summary']) {
    expect(jobs[name].if).toContain("needs.ci_config.outputs.deployment == 'allowed'");
  }
});

test.each([
  ['clean_validation_envs.yml', 'plan', 'cleanup'],
  ['clean_validation_envs.yml', 'clean', 'cleanup'],
  ['clean_validation_envs.yml', 'validate', 'validation'],
  ['pr_comment_bot.yml', 'destroy_pr_env', 'cleanup'],
  ['pr_comment_bot.yml', 'destroy_branch_env', 'cleanup'],
  ['cli-package.yml', 'build', 'deployment'],
  ['lets_encrypt.yml', 'renew_letsencrypt_certs', 'deployment']
])('%s %s checks secret activation before every subsequent step', (file, name, purpose) => {
  const job = workflow(file).jobs[name];
  const index = job.steps.findIndex(step => step.id === 'scope');
  expect(index).toBeGreaterThanOrEqual(1);
  expect(job.steps[index].run).toContain(`prepare --purpose ${purpose}`);
  for (const step of job.steps.slice(index + 1)) {
    expect(step.if).toContain(`steps.scope.outputs.${purpose} == 'allowed'`);
  }
});

test('settings use secrets in environment jobs and decisions in job conditions', () => {
  for (const file of ['deploy_tre_reusable.yml', 'deploy_tre.yml', 'deploy_tre_branch.yml', 'pr_comment_bot.yml', 'clean_validation_envs.yml', 'cli-package.yml', 'lets_encrypt.yml']) {
    const definition = workflow(file);
    expect(JSON.stringify(definition)).not.toMatch(/vars\.CI_(NAMESPACE|DEPLOYMENTS_ENABLED|CLEANUP_ENABLED|MAIN_ENABLED)/);
    for (const job of Object.values(definition.jobs)) {
      expect(job.if || '').not.toMatch(/secrets\.|env\.CI_/);
      if (!job.env?.CI_NAMESPACE) continue;
      expect(job.environment).toBeDefined();
      expect(job.env.CI_NAMESPACE).toBe('${{ secrets.CI_NAMESPACE }}');
      expect(job.env.CI_LOCATION).toContain('secrets.LOCATION');
      expect(job.env.CI_LOCATION).toContain("secrets.CI_NAMESPACE == '' && vars.LOCATION");
    }
  }
});
