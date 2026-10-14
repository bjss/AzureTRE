# Opt-in CI repository isolation

CI can share an Azure subscription across repositories without sharing deployment names or Terraform state.
This is an operational ownership boundary. It does not restrict what code with subscription-wide credentials can do.
Use separate application registrations for CI deployments and tests where they hold mutable configuration.

## Configuration

Set these **repository Actions variables**, not environment variables. Job conditions run before environment variables become available.
Do not override these settings in a GitHub environment.

| Variable | CGI value | Behaviour when the namespace is set |
| --- | --- | --- |
| `CI_NAMESPACE` | `cgi` | Enables namespaced names and repository ownership checks. |
| `CI_DEPLOYMENTS_ENABLED` | `false` initially | Set to `true` to enable deployment and E2E jobs. |
| `CI_CLEANUP_ENABLED` | `false` initially | Set to `true` to enable scheduled and slash-command cleanup. |
| `CI_MAIN_ENABLED` | `false` initially | Main deployments, certificate renewal and CLI packaging also require this switch. |
| `LOCATION` | Your chosen Azure region | Required for deterministic names. The region can match another repository. |
| `AZURE_ENVIRONMENT` | `AzureCloud` | Optional. Defaults to the public Azure cloud. |

An absent `CI_NAMESPACE` retains existing names and activation behaviour. It does not activate the new switches.
The legacy cleanup and recovery paths reject resources carrying the new ownership markers.
Namespaces contain two or three lowercase letters. `tre` is reserved for existing environments.
Keep the namespace fixed after creating resources. Changing namespace, repository ID, region or cloud creates a different environment identity.

Credentials remain GitHub secrets. Existing environment credentials can remain in `CICD`.
In namespaced mode, generated resource configuration takes precedence over the legacy resource-name secrets.
The workflow resolves names without credentials and distributes them as job outputs.

## Names and ownership

Names derive from the namespace, immutable GitHub repository ID, Git ref, Azure cloud and region.
An organisation rename does not enter that calculation. Check OIDC federation separately after a rename or transfer.
A newly created replacement repository has a different ID and therefore different environments.

For a suffix `12345678`, the CGI names are:

| Resource | Name |
| --- | --- |
| TRE and container registry | `cgi12345678` |
| Core resource group | `rg-cgi12345678` |
| Management resource group | `rg-cgi12345678-mgmt` |
| Terraform state storage and optional encryption vault | `cgi12345678mgmt` |
| Devcontainer image tag | `cgi12345678` |

Main, each branch and each PR have separate identities. Each environment uses its own registry for its build cache.
Core and management groups carry `ci_namespace`, `ci_repository_id` and `ci_git_ref` tags.
New management groups receive ownership tags during bootstrap, before storage creation.
Existing roots with missing or conflicting ownership cannot be adopted automatically.
Workspace and service groups belong to the verified core through their bounded resource-name prefixes.

Scheduled cleanup selects only matching repository and namespace ownership, then checks branch or PR activity.
It rechecks paired root ownership before stopping or deleting resources.
Namespaced main environments are persistent: hourly cleanup does not delete their workspace groups.
The legacy disposable-group validation workflow remains available only in legacy mode.
Validate namespaced cleanup using a disposable branch deployment and its explicit cleanup command.

## Activation procedure

Before activation, review every cleanup implementation sharing the subscription.
Install the ownership exclusion safeguard in those repositories, or keep their cleanup disabled during validation.
Do not publish or trigger an upstream change as part of configuring this fork.

1. Merge the implementation while inherited Actions remain disabled.
2. Configure `CI_NAMESPACE=cgi` and set all three switches to `false`.
3. Configure `LOCATION`, credentials and the matching `CICD` OIDC federation subject.
4. Check that application registrations and custom domains will not overwrite another deployment's configuration.
5. Enable GitHub Actions and validate ordinary PR checks.
6. Set `CI_DEPLOYMENTS_ENABLED=true` after reviewing the exact deployment commit.
7. Dispatch a trusted disposable branch deployment.
8. Confirm generated names, state storage, cache location and root ownership tags.
9. Enable cleanup only after inspecting its candidate resource groups.
10. Validate explicit cleanup of the disposable branch while confirming other repositories' groups remain untouched.
11. Enable main deployments separately if required.

Disabling a switch prevents new jobs. It does not cancel a job already running.
GitHub concurrency queues are repository-local. They do not coordinate separate repositories, so ownership checks are required in both.
Shared Entra permissions, network limits and subscription quotas remain shared constraints.
No network access or Azure roles are expanded by this code change.

## Local validation

Run the DevOps helper suite and workflow contract suite before deployment:

```sh
python3 -m unittest discover -s devops/tests -v
npm --prefix .github/scripts ci
npm --prefix .github/scripts test -- --runInBand
```

These tests use synthetic identities and mocked Azure calls. They do not establish live Azure isolation or successful deployment.
