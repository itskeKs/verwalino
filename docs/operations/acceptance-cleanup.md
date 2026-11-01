# Remove a disposable acceptance stack

The session that starts an acceptance or verifier run removes its resources after the run,
including failure or abandonment. This is the final step of the
[acceptance brief](../acceptance/README.md#mandatory-last-step-cleanup); the ownership and
shared-resource limits are in [Session cleanup](../../AGENTS.md#session-cleanup).
These commands document cleanup, not authorization to start a stack.

Use this procedure only for your separately authorized disposable portable project `verwalino`
on a host where that project is not production and no other session uses it. The installer
hard-codes `--project-name verwalino`. Never use it on the hosted server, where
`verwalino` is production, or against `hausverwaltung`, shared test project `infra`, or another
session's project. Do not use global Docker prune commands or delete build caches shared by
other work. A matching project label alone does not prove that a resource is yours.

## Check ownership before removal

Confirm the Docker context is the one used for the run. Inspect the project-labelled resources:

```sh
docker context show
docker ps --all --filter 'label=com.docker.compose.project=verwalino'
docker volume ls --filter 'label=com.docker.compose.project=verwalino'
docker network ls --filter 'label=com.docker.compose.project=verwalino'
docker image ls --filter 'label=com.docker.compose.project=verwalino'
```

Confirm every resource selected for removal was created by this session and no other session
or production service uses it. Check mounts on the listed containers with
`docker container inspect <container-id>`: anonymous volumes may have no project label and
are removed with their owning container below. Preserve bind-mounted source files and tracked
documents. Leave uncertain or shared resources untouched and report what remains; never infer
ownership from a directory, port or name prefix alone.

## Remove only the confirmed project resources

Stop your own dev servers, installer-harness process groups and other background children first.
Then run these commands in order, after the ownership check above. Each loop handles an empty
list without passing an empty argument to a removal command. Stop on an error and inspect it;
do not force removal of shared volumes, networks or images.

```sh
docker container ls --all --quiet --filter 'label=com.docker.compose.project=verwalino' |
  while IFS= read -r resource; do
    docker container rm --force --volumes "$resource" || exit 1
  done

docker volume ls --quiet --filter 'label=com.docker.compose.project=verwalino' |
  while IFS= read -r resource; do
    docker volume rm "$resource" || exit 1
  done

docker network ls --quiet --filter 'label=com.docker.compose.project=verwalino' |
  while IFS= read -r resource; do
    docker network rm "$resource" || exit 1
  done

docker image ls --quiet --no-trunc --filter 'label=com.docker.compose.project=verwalino' |
  sort -u |
  while IFS= read -r resource; do
    docker image rm "$resource" || exit 1
  done
```

`--volumes` on container removal includes its anonymous volumes. The separate volume loop
removes the project's named volumes, including `pgdata` and `documents`. Remove local images
only if this run created them and they are used exclusively by this project; leave downloaded
base/release images, images without that project label and shared build cache alone. Never
widen the project filter.

Repeat the four label-filtered listings above: the disposable
acceptance project and its owned resources must be gone. Check that your process groups have
exited and their ports are free; stop only processes you started. Remove your scratch files,
temporary screenshots, private acceptance config and inactive installer locks after no process
uses them. Never remove `/tmp/verwalino-testdb.lock` while another run holds it. Worktree and
local-branch removal waits until the PR is merged or closed, after checking its state, a clean
tree and stopped processes. Finish with one cleanup line in the status/report, never a file
in the repository.
