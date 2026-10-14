const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const yaml = require('js-yaml');

const root = path.resolve(__dirname, '../..');
const workflow = name => yaml.load(fs.readFileSync(path.join(root, '.github/workflows', name), 'utf8'));

// Evaluate only checked-in job expressions using synthetic contexts.
const enabled = (expression, vars, inputs = {}) => Function('vars', 'inputs',
  `return (${expression.replace(/^\$\{\{/, '').replace(/\}\}$/, '')});`)(vars, inputs);

function names(namespace, repository, ref = 'refs/pull/1/merge') {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ci-repository-'));
  const output = path.join(directory, 'output');
  try {
    execFileSync('python3', ['devops/scripts/ci_repository_scope.py', 'resolve'], {
      cwd: root,
      env: { ...process.env, CI_NAMESPACE: namespace, GITHUB_REPOSITORY_ID: repository,
        CI_GIT_REF: ref, CI_LOCATION: 'switzerlandnorth', CI_AZURE_ENVIRONMENT: 'AzureCloud', GITHUB_OUTPUT: output }
    });
    return fs.existsSync(output) ? Object.fromEntries(fs.readFileSync(output, 'utf8').trim().split('\n').map(line => line.split('='))) : {};
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('legacy configuration emits no overrides; CGI names separate repositories and main', () => {
  expect(names('', '1')).toEqual({});
  const first = names('cgi', '1');
  const other = names('cgi', '2');
  const main = names('cgi', '1', 'refs/heads/main');
  for (const key of Object.keys(first)) {
    expect(first[key]).not.toBe(other[key]);
    expect(first[key]).not.toBe(main[key]);
  }
  expect(first.tre_id).toMatch(/^cgi[0-9a-f]{8}$/);
  expect(first.mgmt_storage_account_name.length).toBeLessThanOrEqual(24);
  expect(first.encryption_kv_name.length).toBeLessThanOrEqual(24);
});

test('deployment gates preserve legacy behaviour and require explicit namespace opt-in', () => {
  const expression = workflow('deploy_tre_reusable.yml').jobs.ci_config.if;
  const branch = { ciGitRef: 'refs/heads/example' };
  const main = { ciGitRef: 'refs/heads/main' };
  expect(enabled(expression, { CI_NAMESPACE: '' }, main)).toBe(true);
  for (const value of ['', 'false', 'yes']) {
    expect(enabled(expression, { CI_NAMESPACE: 'cgi', CI_DEPLOYMENTS_ENABLED: value }, branch)).toBe(false);
  }
  const vars = { CI_NAMESPACE: 'cgi', CI_DEPLOYMENTS_ENABLED: 'true', CI_MAIN_ENABLED: 'false' };
  expect(enabled(expression, vars, branch)).toBe(true);
  expect(enabled(expression, vars, main)).toBe(false);
  expect(enabled(expression, { ...vars, CI_MAIN_ENABLED: 'true' }, main)).toBe(true);
});

test('every reusable job consuming generated names depends on the resolver', () => {
  const jobs = workflow('deploy_tre_reusable.yml').jobs;
  for (const [name, job] of Object.entries(jobs)) {
    if (JSON.stringify(job).includes('needs.ci_config.outputs')) {
      expect({ name, dependency: job.needs.includes('ci_config') }).toEqual({ name, dependency: true });
    }
    for (const step of job.steps || []) {
      if (step.uses !== './.github/actions/devcontainer_run_command') continue;
      for (const key of ['TRE_ID', 'ACR_NAME', 'MGMT_RESOURCE_GROUP_NAME', 'MGMT_STORAGE_ACCOUNT_NAME', 'CI_CACHE_ACR_NAME']) {
        expect(step.with[key]).toContain('needs.ci_config.outputs.');
        expect(step.with[key]).toContain("vars.CI_NAMESPACE == '' && secrets.");
      }
      expect(step.with.CI_GIT_REF).toBe('${{ inputs.ciGitRef }}');
    }
  }
  for (const name of ['e2e_tests_smoke', 'e2e_tests_custom']) {
    expect(jobs[name].if).toContain("needs.ci_config.result == 'success'");
  }
});

test('cleanup requires its own switch and legacy validation cannot create unowned CGI groups', () => {
  const jobs = workflow('clean_validation_envs.yml').jobs;
  expect(jobs.plan.if).toContain("vars.CI_CLEANUP_ENABLED == 'true'");
  expect(jobs.validate.if).toContain("vars.CI_NAMESPACE == ''");
  const bot = workflow('pr_comment_bot.yml').jobs;
  for (const name of ['destroy_pr_env', 'destroy_branch_env']) {
    expect(bot[name].if).toContain("vars.CI_CLEANUP_ENABLED == 'true'");
  }
});
