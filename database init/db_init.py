"""
数据库初始化脚本（PostgreSQL）

功能：
1. 连接 PostgreSQL 默认维护库 postgres，检查并自动创建目标数据库 codemax_db
2. 连接目标数据库，执行 full_init.sql 中的建表 / 初始化语句

用法：
    pip install -r ../requirements.txt
    python db_init.py

注意：
    CREATE DATABASE 不能在事务块内执行，也不能在已连接的目标库上执行，
    因此必须分两步：先连 postgres 维护库建库（autocommit），再连目标库建表。
"""
import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

# 读取 ../.env 中的数据库配置
load_dotenv(dotenv_path="../.env")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "codemax_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "full_init.sql")

print(f"目标数据库: {DB_NAME} @ {DB_HOST}:{DB_PORT} (user={DB_USER})")


def database_exists(conn, db_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        return cur.fetchone() is not None


def create_database(conn, db_name: str) -> None:
    # 使用 psycopg2.sql.Identifier 避免库名拼接 SQL 注入；CREATE DATABASE 不支持参数化
    from psycopg2.sql import Identifier

    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE {Identifier(db_name).as_string(conn)}")


def main() -> None:
    # 第一步：连接默认维护库 postgres，创建目标数据库（如不存在）
    # autocommit=True 是为了让 CREATE DATABASE 不在事务块内执行
    admin_conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database="postgres",
        user=DB_USER,
        password=DB_PASSWORD,
    )
    admin_conn.autocommit = True
    try:
        if database_exists(admin_conn, DB_NAME):
            print(f"[1/2] 数据库 {DB_NAME} 已存在，跳过创建")
        else:
            create_database(admin_conn, DB_NAME)
            print(f"[1/2] 数据库 {DB_NAME} 创建成功")
    finally:
        admin_conn.close()

    # 第二步：连接目标数据库，执行建表 / 初始化 SQL
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        options="-c client_encoding=UTF8",
    )
    conn.autocommit = False
    try:
        with (
            conn.cursor(cursor_factory=RealDictCursor) as cur,
            open(SCHEMA_FILE, "r", encoding="utf-8") as sql_file,
        ):
            cur.execute(sql_file.read())
        conn.commit()
        print(f"[2/2] 已在 {DB_NAME} 中执行 {SCHEMA_FILE}，建表完成")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
