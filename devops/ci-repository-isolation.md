# Opt-in CI repository isolation with secrets

CI can share an Azure subscription across repositories without sharing deployment names or Terraform state.
This is an operational ownership boundary. It does not restrict what code with subscription-wide credentials can do.
Use separate application registrations for CI deployments and tests where they hold mutable configuration.

## Configuration

Set these **Actions secrets** in the selected deployment environment, normally `CICD`.
Repository secrets are also supported. Environment secrets take precedence.
A preparation step reads the secrets inside each environment job before allowing Azure access.

| Secret | CGI value | Behaviour when the namespace is set |
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
Each job derives its resource names locally and registers log masks before exporting them through `GITHUB_ENV`.
Only non-sensitive activation decisions pass between jobs. Namespaced resource names never pass through job outputs.
GitHub can suppress job outputs containing secrets, so changing `vars` to `secrets` alone would not work.
Legacy mode still accepts the existing `LOCATION` and `AZURE_ENVIRONMENT` variables when corresponding secrets are absent.
Namespaced mode requires the `LOCATION` secret. Other existing tuning variables retain their existing behaviour.

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

## Log masking

GitHub masks configured secret values. The resolver also registers masks for generated TRE IDs, suffixes,
resource groups, registry names, storage names and image tags before later steps use them.
Cleanup registers masks for owned environments before printing its inventory or targets.

For the synthetic suffix above, a log line such as `TRE_ID=cgi12345678` becomes `TRE_ID=***`.
Names containing a masked suffix are also redacted, although fixed prefixes and resource types can remain visible.
The workflow uses `allowed` and `blocked` as activation outputs so the secret flag value `true` is never an output.

Masking applies to Actions logs. It does not sanitise uploaded files, test reports, artifacts or PR comments.
Review those outputs separately before activating public deployment runs.
Encoded or otherwise transformed values need their own masks. This prototype does not prove that every tool output is covered.

See GitHub's [secret guidance](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
and [job output behaviour](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idoutputs).

## Activation procedure

Before activation, review every cleanup implementation sharing the subscription.
Install the ownership exclusion safeguard in those repositories, or keep their cleanup disabled during validation.
Do not publish or trigger an upstream change as part of configuring this fork.

1. Keep deployment and cleanup workflows disabled while preparing the fork configuration.
2. Configure the `CI_NAMESPACE` secret as `cgi` and all three switch secrets as `false`.
3. Configure the `LOCATION` secret, credentials and the matching `CICD` OIDC federation subject.
4. Merge the chosen implementation into the fork's `main` branch before enabling deployment workflows.
5. Check application registrations and custom domains, then enable Actions and validate ordinary PR checks.
6. Set `CI_DEPLOYMENTS_ENABLED=true` after reviewing the exact deployment commit.
7. Dispatch a trusted disposable branch deployment.
8. Confirm generated names, state storage, cache location and root ownership tags.
9. Enable cleanup only after inspecting its candidate resource groups.
10. Validate explicit cleanup of the disposable branch while confirming other repositories' groups remain untouched.
11. Enable main deployments separately if required.

Disabling a switch blocks new Azure work when preparation reads it. It does not cancel work already authorised by a running job.
Configuration jobs still run to read environment secrets. Certificate renewal shares the main deployment concurrency queue.
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

These tests use synthetic identities, mocked Azure calls and workflow contracts.
They check mask registration and output routing, but do not exercise the GitHub runner masking engine.
Live Actions masking, Azure isolation and successful deployment remain separate validation steps.
