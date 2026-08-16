#!/bin/sh

set -eu

destination=${1:?"usage: install-linux-ci-tools.sh DESTINATION"}
download_dir=$(mktemp -d)

mkdir -p "$destination"

download() {
  url=$1
  output=$2
  expected_sha256=$3

  curl --fail --location --silent --show-error "$url" --output "$output"
  actual_sha256=$(shasum -a 256 "$output" | awk '{print $1}')
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    printf 'checksum mismatch for %s\n' "$url" >&2
    exit 1
  fi
}

download \
  "https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz" \
  "$download_dir/shellcheck.tar.xz" \
  "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"
tar -xJf "$download_dir/shellcheck.tar.xz" -C "$download_dir"
install -m 0755 "$download_dir/shellcheck-v0.11.0/shellcheck" "$destination/shellcheck"

download \
  "https://github.com/mvdan/sh/releases/download/v3.13.1/shfmt_v3.13.1_linux_amd64" \
  "$download_dir/shfmt" \
  "fb096c5d1ac6beabbdbaa2874d025badb03ee07929f0c9ff67563ce8c75398b1"
install -m 0755 "$download_dir/shfmt" "$destination/shfmt"

download \
  "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz" \
  "$download_dir/actionlint.tar.gz" \
  "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
tar -xzf "$download_dir/actionlint.tar.gz" -C "$download_dir" actionlint
install -m 0755 "$download_dir/actionlint" "$destination/actionlint"

download \
  "https://github.com/zizmorcore/zizmor/releases/download/v1.29.0/zizmor-x86_64-unknown-linux-gnu.tar.gz" \
  "$download_dir/zizmor.tar.gz" \
  "dd96df044a6e8538d5f423790f453bdd03d49e5b2bcc38214acc41a2f1297839"
tar -xzf "$download_dir/zizmor.tar.gz" -C "$download_dir" zizmor
install -m 0755 "$download_dir/zizmor" "$destination/zizmor"

download \
  "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz" \
  "$download_dir/gitleaks.tar.gz" \
  "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
tar -xzf "$download_dir/gitleaks.tar.gz" -C "$download_dir" gitleaks
install -m 0755 "$download_dir/gitleaks" "$destination/gitleaks"
