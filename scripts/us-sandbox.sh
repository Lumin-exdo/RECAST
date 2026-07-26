#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  set -- /bin/bash
fi

sandbox_tool_dir=${SANDBOX_TOOL_DIR:-$HOME/.local/a100-sandbox-tools/usr/bin}
sandbox_lib_dir=${SANDBOX_LIB_DIR:-$HOME/.local/a100-sandbox-tools/usr/lib/x86_64-linux-gnu}
rootlesskit_bin=${SANDBOX_ROOTLESSKIT:-$sandbox_tool_dir/rootlesskit}
bwrap_bin=${SANDBOX_BWRAP:-$sandbox_tool_dir/bwrap}
sandbox_backend=${SANDBOX_BACKEND:-bwrap}
sandbox_network=${SANDBOX_NETWORK:-host}
sandbox_hostname=${SANDBOX_HOSTNAME:-us-sandbox}
sandbox_tz=${SANDBOX_TZ:-America/Chicago}
sandbox_lang=${SANDBOX_LANG:-C.UTF-8}
sandbox_user=${SANDBOX_USER:-sandbox}
sandbox_home=${SANDBOX_HOME:-/home/sandbox}
share_host_pid=${SANDBOX_SHARE_HOST_PID:-1}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
default_project_root=$(dirname "$script_dir")
project_root=${SANDBOX_PROJECT_ROOT:-$default_project_root}
project_root=$(realpath -m "$project_root")
sandbox_laq_root=${SANDBOX_LAQ_ROOT:-$(dirname "$project_root")}
sandbox_project_dst=${SANDBOX_PROJECT_DST:-/mnt/laq/RECAST}
extra_ro_dirs=${SANDBOX_EXTRA_RO_DIRS:-}
extra_rw_dirs=${SANDBOX_EXTRA_RW_DIRS:-$sandbox_laq_root/LongMemEval:$sandbox_laq_root/MemEvolve:$sandbox_laq_root/STALE:$sandbox_laq_root/amem_eval:$sandbox_laq_root/cup_mem:$sandbox_laq_root/experiments:$sandbox_laq_root/lightmem_repo:$sandbox_laq_root/mem0_eval:$sandbox_laq_root/naive_rag:$sandbox_laq_root/recast-bench}
claude_state_src=${SANDBOX_CLAUDE_STATE_SRC:-$HOME/.claude}
claude_config_src=${SANDBOX_CLAUDE_CONFIG_SRC:-$HOME/.claude.json}
claude_bin_src=${SANDBOX_CLAUDE_BIN_SRC:-$HOME/.local/bin}
claude_share_src=${SANDBOX_CLAUDE_SHARE_SRC:-$HOME/.local/share/claude}
venv_src=${SANDBOX_VENV_SRC:-$sandbox_laq_root/venv}
ssh_key_src=${SANDBOX_SSH_KEY_SRC:-$HOME/.ssh/id_ed25519}
ssh_pubkey_src=${SANDBOX_SSH_PUBKEY_SRC:-$HOME/.ssh/id_ed25519.pub}
ssh_known_hosts_src=${SANDBOX_SSH_KNOWN_HOSTS_SRC:-$HOME/.ssh/known_hosts}
git_config_src=${SANDBOX_GIT_CONFIG_SRC:-$HOME/.gitconfig}
claude_state_dst=${SANDBOX_CLAUDE_STATE_DST:-/home/sandbox/.claude}
claude_config_dst=${SANDBOX_CLAUDE_CONFIG_DST:-/home/sandbox/.claude.json}
claude_exec_dst=${SANDBOX_CLAUDE_EXEC_DST:-/home/sandbox/.local/bin/claude}
current_uid=$(id -u)
current_gid=$(id -g)

for required_bin in "$bwrap_bin"; do
  if [[ ! -x "$required_bin" ]]; then
    printf 'sandbox tool missing or not executable: %s\n' "$required_bin" >&2
    exit 1
  fi
done
if [[ "$sandbox_backend" == "rootlesskit" && ! -x "$rootlesskit_bin" ]]; then
  printf 'rootlesskit backend requested but tool is missing: %s\n' "$rootlesskit_bin" >&2
  exit 1
fi

append_env_if_set() {
  local key=$1
  local value=${!key-}
  if [[ -n "${value:-}" ]]; then
    bwrap_args+=(--setenv "$key" "$value")
  fi
}

is_allowed_system_path() {
  case "$1" in
    /usr|/usr/*|/lib|/lib/*|/lib64|/lib64/*|/etc/nsswitch.conf|/etc/protocols|/etc/services|/etc/gai.conf|/etc/ssl|/etc/ssl/*|/usr/share/zoneinfo|/usr/share/zoneinfo/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_owned_by_user() {
  [[ -e "$1" ]] && [[ "$(stat -c '%u' "$1")" == "$current_uid" ]]
}

if ! is_owned_by_user "$project_root"; then
  printf 'refusing sandbox project root not owned by current user: %s\n' "$project_root" >&2
  exit 1
fi

if ! is_owned_by_user "$claude_state_src"; then
  printf 'refusing claude state dir not owned by current user: %s\n' "$claude_state_src" >&2
  exit 1
fi

if ! is_owned_by_user "$claude_config_src"; then
  printf 'refusing claude config not owned by current user: %s\n' "$claude_config_src" >&2
  exit 1
fi

if ! is_owned_by_user "$claude_bin_src"; then
  printf 'refusing claude bin dir not owned by current user: %s\n' "$claude_bin_src" >&2
  exit 1
fi

if ! is_owned_by_user "$claude_share_src"; then
  printf 'refusing claude share dir not owned by current user: %s\n' "$claude_share_src" >&2
  exit 1
fi

if ! is_owned_by_user "$venv_src"; then
  printf 'refusing venv dir not owned by current user: %s\n' "$venv_src" >&2
  exit 1
fi

claude_real_bin=$(readlink -f "$claude_bin_src/claude")
if ! is_owned_by_user "$claude_real_bin"; then
  printf 'refusing claude executable not owned by current user: %s\n' "$claude_real_bin" >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

mkdir -p "$tmpdir/etc"

cat >"$tmpdir/etc/hostname" <<EOF
$sandbox_hostname
EOF

cat >"$tmpdir/etc/hosts" <<'EOF'
127.0.0.1 localhost
::1 localhost
EOF

cat >"$tmpdir/etc/resolv.conf" <<'EOF'
nameserver 10.0.2.3
options edns0
EOF

cat >"$tmpdir/etc/os-release" <<'EOF'
NAME="Ubuntu"
PRETTY_NAME="Ubuntu 24.04 LTS"
ID=ubuntu
VERSION_ID="24.04"
HOME_URL="https://ubuntu.com/"
SUPPORT_URL="https://ubuntu.com/support"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
EOF

cat >"$tmpdir/etc/issue" <<'EOF'
Ubuntu 24.04 LTS \n \l
EOF

cat >"$tmpdir/ssh_config" <<'EOF'
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  HostKeyAlias github.com
EOF

# Hide host/WSL kernel branding from programs that read procfs. The uname(2)
# system call is controlled by the host kernel and cannot be spoofed by
# bubblewrap; this is therefore best-effort rather than an absolute guarantee.
cat >"$tmpdir/proc-version" <<'EOF'
Linux version 6.8.0-generic (buildd@ubuntu) #1 SMP PREEMPT_DYNAMIC Ubuntu
EOF
cat >"$tmpdir/proc-osrelease" <<'EOF'
6.8.0-generic
EOF

cat >"$tmpdir/etc/passwd" <<EOF
root:x:0:0:root:/root:/bin/bash
$sandbox_user:x:$current_uid:$current_gid:Sandbox User:$sandbox_home:/bin/bash
EOF

cat >"$tmpdir/etc/group" <<EOF
root:x:0:
$sandbox_user:x:$current_gid:
EOF

printf '%s\n' "$(cat /proc/sys/kernel/random/uuid | tr -d '-')" >"$tmpdir/etc/machine-id"

if [[ -e /usr/share/zoneinfo/$sandbox_tz ]]; then
  mkdir -p "$tmpdir/zoneinfo"
else
  sandbox_tz=UTC
fi

extra_ro_args=()
if [[ -n "$extra_ro_dirs" ]]; then
  IFS=':' read -r -a extra_ro_list <<<"$extra_ro_dirs"
  for extra_dir in "${extra_ro_list[@]}"; do
    [[ -n "$extra_dir" ]] || continue
    if ! is_allowed_system_path "$extra_dir" && ! is_owned_by_user "$extra_dir"; then
      printf 'refusing extra read-only path not owned by current user: %s\n' "$extra_dir" >&2
      exit 1
    fi
    extra_ro_args+=(--ro-bind-try "$extra_dir" "$extra_dir")
  done
fi

extra_rw_args=()
if [[ -n "$extra_rw_dirs" ]]; then
  IFS=':' read -r -a extra_rw_list <<<"$extra_rw_dirs"
  for extra_dir in "${extra_rw_list[@]}"; do
    [[ -n "$extra_dir" ]] || continue
    if ! is_owned_by_user "$extra_dir"; then
      printf 'refusing extra writable path not owned by current user: %s\n' "$extra_dir" >&2
      exit 1
    fi
    extra_rw_args+=(--bind "$extra_dir" "$extra_dir")
  done
fi

auth_args=()
for auth_pair in \
  "$ssh_key_src:/home/sandbox/.ssh/id_ed25519" \
  "$ssh_pubkey_src:/home/sandbox/.ssh/id_ed25519.pub" \
  "$ssh_known_hosts_src:/home/sandbox/.ssh/known_hosts" \
  "$git_config_src:/home/sandbox/.gitconfig"; do
  auth_src=${auth_pair%%:*}
  auth_dst=${auth_pair#*:}
  if [[ -e "$auth_src" ]]; then
    if ! is_owned_by_user "$auth_src"; then
      printf 'refusing Git/SSH auth file not owned by current user: %s\n' "$auth_src" >&2
      exit 1
    fi
    auth_args+=(--ro-bind "$auth_src" "$auth_dst")
  fi
done

bwrap_args=(
  --unshare-uts
  --unshare-ipc
  --new-session
  --hostname "$sandbox_hostname"
  --clearenv
  --tmpfs /
  --proc /proc
  --ro-bind-try "$tmpdir/proc-version" /proc/version
  --ro-bind-try "$tmpdir/proc-osrelease" /proc/sys/kernel/osrelease
  --dev /dev
  --tmpfs /tmp
  --tmpfs /var
  --tmpfs /run
  --tmpfs /home
  --tmpfs /sys
  --dir /usr
  --ro-bind /usr /usr
  --symlink usr/bin /bin
  --symlink usr/sbin /sbin
  --symlink usr/lib /lib
  --symlink usr/lib64 /lib64
  --ro-bind-try /usr/share/zoneinfo /usr/share/zoneinfo
  --ro-bind-try /etc/nsswitch.conf /etc/nsswitch.conf
  --ro-bind-try /etc/protocols /etc/protocols
  --ro-bind-try /etc/services /etc/services
  --ro-bind-try /etc/gai.conf /etc/gai.conf
  --ro-bind-try /etc/ssh /etc/ssh
  --tmpfs /etc
  --ro-bind-try /etc/ssl /etc/ssl
  --ro-bind /usr/share/zoneinfo/"$sandbox_tz" /etc/localtime
  --bind "$tmpdir/etc/hostname" /etc/hostname
  --bind "$tmpdir/etc/hosts" /etc/hosts
  --bind "$tmpdir/etc/resolv.conf" /etc/resolv.conf
  --bind "$tmpdir/etc/os-release" /etc/os-release
  --bind "$tmpdir/etc/issue" /etc/issue
  --bind "$tmpdir/etc/passwd" /etc/passwd
  --bind "$tmpdir/etc/group" /etc/group
  --bind "$tmpdir/etc/machine-id" /etc/machine-id
  --dir "$sandbox_home"
  --dir /home/sandbox/.local
  --dir /home/sandbox/.local/share
  --dir /home/sandbox/.local/bin
  --dir /home/sandbox/.ssh
  --bind "$claude_state_src" "$claude_state_dst"
  --bind "$claude_config_src" "$claude_config_dst"
  --ro-bind "$claude_share_src" /home/sandbox/.local/share/claude
  --bind "$claude_real_bin" "$claude_exec_dst"
  --ro-bind "$tmpdir/ssh_config" /home/sandbox/.ssh/config
  --setenv HOME "$sandbox_home"
  --setenv USER "$sandbox_user"
  --setenv LOGNAME "$sandbox_user"
  --setenv SHELL /bin/bash
  --setenv PATH "$venv_src/bin:/home/sandbox/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  --setenv VIRTUAL_ENV "$venv_src"
  --setenv LANG "$sandbox_lang"
  --setenv LC_ALL "$sandbox_lang"
  --setenv TZ "$sandbox_tz"
  --setenv TERM "${TERM:-xterm-256color}"
)

if [[ "$share_host_pid" != 1 ]]; then
  bwrap_args+=(--unshare-pid)
fi

if [[ "$sandbox_backend" == "bwrap" && "$sandbox_network" == "isolated" ]]; then
  bwrap_args+=(--unshare-net)
fi

append_env_if_set CLAUDE_CODE_DISABLE_AUTO_MEMORY
append_env_if_set ANTHROPIC_MODEL
append_env_if_set OPENAI_API_KEY
append_env_if_set OPENAI_BASE_URL

bwrap_args+=(
  --bind "$venv_src" "$venv_src"
  --bind "$project_root" "$sandbox_project_dst"
  --chdir "$sandbox_project_dst"
)

bwrap_args+=("${extra_rw_args[@]}")
bwrap_args+=("${extra_ro_args[@]}")
bwrap_args+=("${auth_args[@]}")

if [[ "$sandbox_backend" == "bwrap" ]]; then
  exec env -i PATH="$sandbox_tool_dir:/usr/sbin:/usr/bin:/sbin:/bin" LD_LIBRARY_PATH="$sandbox_lib_dir" TERM="${TERM:-xterm-256color}" \
    "$bwrap_bin" --unshare-user --uid "$current_uid" --gid "$current_gid" "${bwrap_args[@]}" -- "$@"
fi

if [[ "$sandbox_backend" != "rootlesskit" ]]; then
  printf 'unsupported SANDBOX_BACKEND: %s\n' "$sandbox_backend" >&2
  exit 2
fi

exec env -i PATH="$sandbox_tool_dir:/usr/sbin:/usr/bin:/sbin:/bin" LD_LIBRARY_PATH="$sandbox_lib_dir" TERM="${TERM:-xterm-256color}" \
  "$rootlesskit_bin" \
    --copy-up=/etc \
    --copy-up=/run \
    --net=slirp4netns \
    --disable-host-loopback \
    --port-driver=none \
    -- \
    "$bwrap_bin" --unshare-user --uid "$current_uid" --gid "$current_gid" "${bwrap_args[@]}" -- "$@"
