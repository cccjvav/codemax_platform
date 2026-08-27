import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
# Database setup
load_dotenv(dotenv_path="../.env")
print(os.getenv("DB_USER"))

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    database=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("psqlPass"),
    options='-c client_encoding=UTF8'
)
cur = conn.cursor(cursor_factory=RealDictCursor)




with open("full_init.sql", 'r', encoding='utf-8') as sql_file:
    sql_commands = sql_file.read()
    cur.execute(sql_commands)


conn.commit()

cur.close()
conn.close()