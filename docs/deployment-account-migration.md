# GitHub Actions deployment-account migration

The deployment workflows log in as the fixed `deploy` account rather than
`root`. The account has no password login path and receives only the existing
`github-actions` public keys. Its sudo policy permits exactly two service
operations: restarting `alphastock-api.service` and reloading `nginx.service`.

## One-time cutover

Do **not** push the workflow changes first: that push would attempt to use the
new account before it exists. From an existing root OrcaTerm session, upload
this repository's `scripts/provision_deploy_user.sh` using the console's file
manager (or paste the reviewed script into a root-owned temporary file), then:

```bash
sudo bash /path/to/provision_deploy_user.sh
```

The script deliberately keeps root SSH unchanged and copies only keys whose
authorized-keys comment is `github-actions`. It validates the new account can
write the application Git directory, execute the existing virtual environment,
and use precisely scoped sudo commands.

Commit and push the workflow changes only after that script succeeds. Then
verify the GitHub Actions run and the server journal show:

```text
Accepted publickey for deploy ... github-actions
```

## Final hardening, after a successful deployment

Keep an OrcaTerm session open. Remove only the GitHub Actions entries from
`/root/.ssh/authorized_keys`, then set `PermitRootLogin no` and
`PasswordAuthentication no` in a dedicated `/etc/ssh/sshd_config.d/` file.
Validate the SSH configuration with `sshd -t` before reloading SSH. Do not
perform this final step until the new `deploy` workflow has completed
successfully.
