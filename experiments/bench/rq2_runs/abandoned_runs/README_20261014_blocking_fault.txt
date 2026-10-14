ABANDONED partial D-B runs 2026-10-14 (driver fault command blocked the stream 37.9s:
kill/sleep/restart ran synchronously inside exec_shell_command, so no request flowed during
the outage -> zero error records; not an RQ2 dropout measurement). Fixed by detaching the
restart on B via Start-Process; relaunched fresh.
