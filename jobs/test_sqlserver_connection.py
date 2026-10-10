"""Connectivity test for the on-premise SQL Server (step 1 of the Azure File Share -> SQL Server pipeline).

Runs four checks and stops at the first failure with a hint on how to fix it:
    1. Network   - can we open a TCP connection to host:port?
    2. Login     - does SQL Server accept the username/password (SQL authentication)?
    3. Server    - which server/version/database are we on, and which IP does the server see us as?
    4. Database  - does the target database exist, and may this login create tables in it?
With --write-test it also creates, fills and reads a #temp table (dropped automatically).

Settings come from environment variables, falling back to the project's .env:
    MSSQL_HOST      default: host.docker.internal (= the laptop's LAN IP, kept current by Docker Desktop)
    MSSQL_PORT      default: 1433
    MSSQL_DB        database to check, e.g. master or GridEnergy
    MSSQL_USER / MSSQL_PASSWORD   a SQL Server login (not a Windows account)

Usage:
    python jobs/test_sqlserver_connection.py                       # uses .env
    python jobs/test_sqlserver_connection.py --host 192.168.1.50 # connect by the laptop's IP
    python jobs/test_sqlserver_connection.py --use-lan-ip          # detect this machine's LAN IP and use it
    python jobs/test_sqlserver_connection.py --write-test
    docker compose exec jupyter python /opt/spark-jobs/test_sqlserver_connection.py

Exit codes: 0 = all checks passed, 1 = a check failed, 2 = configuration error.
"""

import argparse
import os
import socket
import sys
import time
from pathlib import Path

import pymssql

# Helpers live next to this file: works with `python jobs/<script>.py` and `python -m jobs.<script>`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_utils import load_dotenv

PASS, FAIL = "PASS", "FAIL"


def detect_lan_ip() -> str:
    """This machine's primary IPv4 address (the one used for outbound traffic). No packet is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Test the connection to the on-premise SQL Server.")
    parser.add_argument("--host", default=os.environ.get("MSSQL_HOST", "host.docker.internal"),
                        help="Server host name or IP (default: MSSQL_HOST).")
    parser.add_argument("--use-lan-ip", action="store_true",
                        help="Ignore --host and connect to this machine's LAN IP (run on the SQL Server machine).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MSSQL_PORT", "1433")))
    parser.add_argument("--database", default=os.environ.get("MSSQL_DB", "master"))
    parser.add_argument("--user", default=os.environ.get("MSSQL_USER"))
    parser.add_argument("--timeout", type=int, default=10, help="Seconds to wait for network/login (default 10).")
    parser.add_argument("--write-test", action="store_true",
                        help="Also create/insert/select a #temp table (no permanent changes).")
    return parser.parse_args()


def sql_error_text(exc: Exception) -> str:
    """pymssql errors look like (18456, b"Login failed ... DB-Lib error message ..."); keep SQL Server's own message."""
    if exc.args and isinstance(exc.args[0], tuple) and len(exc.args[0]) == 2:
        code, raw = exc.args[0]
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        return f"error {code}: {text.split('DB-Lib error')[0].strip()}"
    return str(exc).splitlines()[0][:200]


def report(status: str, step: str, detail: str) -> None:
    print(f"[{status}] {step:<9} {detail}")


def check_network(host: str, port: int, timeout: int) -> bool:
    try:
        ip = socket.gethostbyname(host)
    except socket.gaierror as exc:
        report(FAIL, "Network", f"cannot resolve '{host}': {exc}")
        print("         Hint: check the host name, or use the IP address directly (--host 192.168.x.x).")
        return False
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError as exc:
        report(FAIL, "Network", f"{host} ({ip}):{port} unreachable: {exc}")
        print("         Hints: SQL Server Configuration Manager -> Protocols -> TCP/IP = Enabled, IPAll TCP Port = 1433,\n"
              "                restart the SQL Server service; allow inbound TCP 1433 in Windows Firewall.")
        return False
    report(PASS, "Network", f"{host} ({ip}):{port} reachable in {(time.perf_counter() - start) * 1000:.0f} ms")
    return True


def connect(host: str, port: int, user: str, password: str, database: str, timeout: int):
    try:
        conn = pymssql.connect(server=host, port=str(port), user=user, password=password, database=database,
                               login_timeout=timeout, timeout=30, appname="spark-homelab-conntest")
    except pymssql.OperationalError as exc:
        message = str(exc)
        report(FAIL, "Login", f"as '{user}' to database '{database}': {sql_error_text(exc)}")
        if "18456" in message:
            print("         Hint: wrong user/password, the login is disabled, or it has no access to this database.\n"
                  "               SQL logins need 'SQL Server and Windows Authentication mode' (already on here).")
        elif "4060" in message:
            print(f"         Hint: database '{database}' doesn't exist or the login can't open it (try --database master).")
        return None
    report(PASS, "Login", f"as '{user}' (SQL authentication)")
    return conn


def check_server(conn) -> None:
    with conn.cursor(as_dict=True) as cur:
        cur.execute("""
            SELECT @@SERVERNAME AS server_name,
                   CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(32)) AS version,
                   CAST(SERVERPROPERTY('Edition') AS nvarchar(64))        AS edition,
                   DB_NAME()      AS current_db,
                   SUSER_SNAME()  AS login_name,
                   c.client_net_address, c.local_tcp_port, c.encrypt_option, c.auth_scheme
            FROM sys.dm_exec_connections AS c
            WHERE c.session_id = @@SPID
        """)
        r = cur.fetchone()
    report(PASS, "Server", f"{r['server_name']} | SQL Server {r['version']} {r['edition']}")
    print(f"         connected to database '{r['current_db']}' as '{r['login_name']}' on port {r['local_tcp_port']}")
    print(f"         server sees this client as {r['client_net_address']} (auth {r['auth_scheme']}, encrypted: {r['encrypt_option']})")


def check_database(conn, database: str) -> bool:
    with conn.cursor(as_dict=True) as cur:
        cur.execute("SELECT name, state_desc FROM sys.databases WHERE name = %s", (database,))
        db = cur.fetchone()
        if not db:
            report(FAIL, "Database", f"'{database}' does not exist")
            return False
        cur.execute("""
            SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CREATE TABLE') AS can_create_table,
                   IS_ROLEMEMBER('db_owner')    AS is_db_owner,
                   IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin
        """)
        perms = cur.fetchone()
    can_create = bool(perms["can_create_table"])
    report(PASS if can_create else FAIL, "Database",
           f"'{database}' is {db['state_desc']} | CREATE TABLE: {'yes' if can_create else 'NO'} | "
           f"db_owner: {'yes' if perms['is_db_owner'] else 'no'} | sysadmin: {'yes' if perms['is_sysadmin'] else 'no'}")
    if perms["is_sysadmin"]:
        print("         Note: this login is sysadmin. For the pipeline, prefer a dedicated login with db_owner on one database.")
    return can_create


def write_test(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE #conn_test (id int, note nvarchar(50), created_at datetime2 DEFAULT SYSUTCDATETIME())")
            cur.executemany("INSERT INTO #conn_test (id, note) VALUES (%d, %s)", [(1, "hello"), (2, "from spark-homelab")])
            cur.execute("SELECT COUNT(*) FROM #conn_test")
            count = cur.fetchone()[0]
        report(PASS, "Write", f"created, inserted and read a #temp table ({count} rows); nothing permanent was changed")
        return True
    except pymssql.Error as exc:
        report(FAIL, "Write", sql_error_text(exc))
        return False


def main() -> int:
    args = parse_args()
    password = os.environ.get("MSSQL_PASSWORD")
    if not args.user or not password:
        print("MSSQL_USER and MSSQL_PASSWORD must be set (environment or .env)")
        return 2
    if password == "change-me":
        print("MSSQL_PASSWORD in .env is still the placeholder 'change-me'; set the real password first.")
        return 2

    host = detect_lan_ip() if args.use_lan_ip else args.host
    print(f"Testing SQL Server at {host}:{args.port}, database '{args.database}', login '{args.user}'\n")

    if not check_network(host, args.port, args.timeout):
        return 1
    conn = connect(host, args.port, args.user, password, args.database, args.timeout)
    if conn is None:
        return 1
    try:
        check_server(conn)
        ok = check_database(conn, args.database)
        if args.write_test:
            ok = write_test(conn) and ok
    finally:
        conn.close()

    print("\nAll checks passed." if ok else "\nConnected, but some checks failed (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
