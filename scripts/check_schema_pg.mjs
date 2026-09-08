/**
 * 用 WASM 版真 PostgreSQL（PGlite）执行 database init/full_init.sql 的体检脚本。
 *
 * 为什么需要它：CI / 沙箱里常常没有 PostgreSQL，建表脚本的语法、外键、索引、
 * DROP 顺序就永远没被真库执行过。这个脚本补上这一段，且不引入 Python 依赖。
 *
 * 用法（一次性装依赖，约 26MB）：
 *     cd /tmp && npm install @electric-sql/pglite
 *     cd - && node scripts/check_schema_pg.mjs /tmp/node_modules/@electric-sql/pglite
 *
 * 不接入 pytest —— 需要 node 与外部包，属可选的深度体检，不是每次改动的必经检查。
 * （tests/test_schema_sync.py 才是每次必跑的：它比对 full_init.sql 与 app/models.py。）
 */
import { readFileSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath, pathToFileURL } from "url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sqlPath = join(repoRoot, "database init", "full_init.sql");

// 允许传目录（如 /tmp/node_modules/@electric-sql/pglite）：ESM 不支持目录导入，得解析到入口文件
async function loadPGlite(spec) {
  let target = spec;
  try {
    const pkg = JSON.parse(readFileSync(join(spec, "package.json"), "utf8"));
    target = pathToFileURL(join(spec, pkg.module ?? pkg.main)).href;
  } catch {
    /* 不是目录就按模块名导入 */
  }
  return (await import(target)).PGlite;
}

const PGlite = await loadPGlite(process.argv[2] ?? "@electric-sql/pglite");
const db = new PGlite();

console.log("PG 版本:", (await db.query("show server_version")).rows[0].server_version);

// 连跑两次：第二次能过，说明 DROP TABLE IF EXISTS 的顺序与幂等性没问题
for (const round of [1, 2]) {
  await db.exec(readFileSync(sqlPath, "utf8"));
  console.log(`第 ${round} 次执行 full_init.sql：OK`);
}

const tables = (
  await db.query("select table_name from information_schema.tables where table_schema='public' order by 1")
).rows.map((r) => r.table_name);
console.log("建出的表:", tables);

const fks = (
  await db.query(`
    select tc.table_name, kcu.column_name, ccu.table_name as ft, ccu.column_name as fc
    from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu on tc.constraint_name = kcu.constraint_name
    join information_schema.constraint_column_usage ccu on tc.constraint_name = ccu.constraint_name
    where tc.constraint_type = 'FOREIGN KEY' order by 1, 2`)
).rows;
console.log("外键:", fks.map((f) => `${f.table_name}.${f.column_name}->${f.ft}.${f.fc}`).join(" | "));

const indexes = (await db.query("select tablename, indexname from pg_indexes where schemaname='public' order by 1, 2")).rows;
console.log("索引数:", indexes.length);

// 冒烟：真写一条数据再读回，确认表可用
const uid = (await db.query("select id from sys_user where username='admin'")).rows[0]?.id;
if (uid) {
  await db.query("insert into sys_diagram (user_id, name, content) values ($1, $2, $3)", [uid, "冒烟测试", "<mxfile/>"]);
  console.log("写入并读回:", (await db.query("select id, name from sys_diagram")).rows[0]);
}
console.log("体检通过");
