
import os
import json
import sys
import time
import traceback
import pymysql

JSON_FILE = "wsyzy_vod.json"
TABLE_NAME = "wsyzy_vod"

# 每批20条
BATCH_SIZE = 20

# 每批最多尝试3次
MAX_RETRIES = 3

DB_HOST = os.environ.get("MYSQL_HOST")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_USER = os.environ.get("MYSQL_USER")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD")
DB_NAME = os.environ.get("MYSQL_DATABASE", "mo5_dj5yx_com")

FIELDS = [
    "last", "id", "tid", "name", "type", "pic",
    "lang", "area", "year", "state", "note",
    "actor", "director", "dl", "des"
]


def val(item, key):
    value = item.get(key)
    return "" if value is None else str(value).strip()


def load_json():
    with open(JSON_FILE, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON顶层必须是数组")

    records = {}

    for item in data:
        if isinstance(item, dict) and item.get("id"):
            records[str(item["id"])] = item

    return list(records.values()), len(data)


def connect_db():
    print("正在连接MySQL...", flush=True)

    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        connect_timeout=30,
        read_timeout=300,
        write_timeout=300,
        autocommit=False
    )

    print("MySQL连接成功", flush=True)
    return conn


def create_table(conn):
    print("正在检查数据表...", flush=True)

    sql = f"""
    CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
        `id` VARCHAR(50) NOT NULL,
        `last` VARCHAR(30) DEFAULT '',
        `tid` VARCHAR(50) DEFAULT '',
        `name` VARCHAR(500) DEFAULT '',
        `type` VARCHAR(100) DEFAULT '',
        `pic` TEXT,
        `lang` VARCHAR(100) DEFAULT '',
        `area` VARCHAR(100) DEFAULT '',
        `year` VARCHAR(20) DEFAULT '',
        `state` VARCHAR(100) DEFAULT '',
        `note` VARCHAR(500) DEFAULT '',
        `actor` TEXT,
        `director` TEXT,
        `dl` LONGTEXT,
        `des` LONGTEXT,
        PRIMARY KEY (`id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    COLLATE=utf8mb4_unicode_ci
    """

    with conn.cursor() as cursor:
        cursor.execute(sql)

    conn.commit()
    print("数据表检查完成", flush=True)


def make_sql():
    columns = ",".join(f"`{x}`" for x in FIELDS)
    placeholders = ",".join(["%s"] * len(FIELDS))

    update_fields = [x for x in FIELDS if x != "id"]

    update_sql = ",".join(
        f"`{x}`=VALUES(`{x}`)" for x in update_fields
    )

    return f"""
        INSERT INTO `{TABLE_NAME}` ({columns})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_sql}
    """


def get_existing_records(conn, batch):
    """
    查询当前批次影片在数据库中的 last 和 dl。
    只查询本批次的ID，避免逐条查询数据库。
    """

    ids = [str(item["id"]) for item in batch]

    if not ids:
        return {}

    placeholders = ",".join(["%s"] * len(ids))

    sql = f"""
        SELECT `id`, `last`, `dl`
        FROM `{TABLE_NAME}`
        WHERE `id` IN ({placeholders})
    """

    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql, ids)
        rows = cursor.fetchall()

    existing = {}

    for row in rows:
        existing[str(row["id"])] = {
            "last": "" if row["last"] is None else str(row["last"]).strip(),
            "dl": "" if row["dl"] is None else str(row["dl"]).strip()
        }

    return existing


def check_changed(item, old):
    """
    双重判断：

    1. last发生变化：更新
    2. dl发生变化：更新
    3. last和dl都没变化：跳过
    """

    if old is None:
        return "new"

    new_last = val(item, "last")
    old_last = old["last"]

    new_dl = val(item, "dl")
    old_dl = old["dl"]

    if new_last != old_last:
        return "updated"

    if new_dl != old_dl:
        return "updated"

    return "skipped"


def sync_batch(conn, sql, batch, batch_no, total_batches):
    """
    先对比数据库，再只写入新增或发生变化的影片。
    """

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(
                f"第 {batch_no}/{total_batches} 批开始，"
                f"共 {len(batch)} 条，尝试 {attempt}/{MAX_RETRIES}",
                flush=True
            )

            conn.ping(reconnect=True)

            # 一次查询本批次全部影片的数据库记录
            existing = get_existing_records(conn, batch)

            new_records = []
            update_records = []
            skipped_records = []

            for item in batch:
                video_id = str(item["id"])
                old = existing.get(video_id)

                status = check_changed(item, old)

                if status == "new":
                    new_records.append(item)

                elif status == "updated":
                    update_records.append(item)

                else:
                    skipped_records.append(item)

            # 合并需要写入的记录
            changed_records = new_records + update_records

            if not changed_records:
                print(
                    f"第 {batch_no}/{total_batches} 批没有变化，"
                    f"跳过 {len(skipped_records)} 条",
                    flush=True
                )

                return conn, len(new_records), len(update_records), len(skipped_records)

            rows = [
                tuple(val(item, field) for field in FIELDS)
                for item in changed_records
            ]

            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)

            conn.commit()

            print(
                f"第 {batch_no}/{total_batches} 批提交成功："
                f"新增 {len(new_records)} 条，"
                f"更新 {len(update_records)} 条，"
                f"跳过 {len(skipped_records)} 条",
                flush=True
            )

            return (
                conn,
                len(new_records),
                len(update_records),
                len(skipped_records)
            )

        except pymysql.err.OperationalError as e:
            print(
                f"第 {batch_no} 批数据库连接异常：{e}",
                flush=True
            )

            try:
                conn.rollback()
            except Exception:
                pass

            try:
                conn.close()
            except Exception:
                pass

            if attempt >= MAX_RETRIES:
                raise

            wait_seconds = attempt * 5

            print(
                f"等待 {wait_seconds} 秒后重新连接并重试...",
                flush=True
            )

            time.sleep(wait_seconds)
            conn = connect_db()

        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    return conn, 0, 0, 0


def sync_data():
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("MySQL环境变量配置不完整")

    records, original_count = load_json()

    print("=" * 45, flush=True)
    print(f"JSON原始记录：{original_count}", flush=True)
    print(f"有效记录：{len(records)}", flush=True)
    print(f"无效记录：{original_count - len(records)}", flush=True)
    print("=" * 45, flush=True)

    if not records:
        print("没有需要同步的数据", flush=True)
        return

    conn = None

    try:
        conn = connect_db()

        create_table(conn)

        sql = make_sql()

        total_batches = (
            len(records) + BATCH_SIZE - 1
        ) // BATCH_SIZE

        processed = 0
        total_inserted = 0
        total_updated = 0
        total_skipped = 0

        print("开始分批同步...", flush=True)

        for batch_no, start in enumerate(
            range(0, len(records), BATCH_SIZE), 1
        ):
            batch = records[start:start + BATCH_SIZE]

            (
                conn,
                inserted,
                updated,
                skipped
            ) = sync_batch(
                conn,
                sql,
                batch,
                batch_no,
                total_batches
            )

            total_inserted += inserted
            total_updated += updated
            total_skipped += skipped

            processed += len(batch)

            print(
                f"总进度：{processed}/{len(records)}，"
                f"完成比例：{processed / len(records) * 100:.2f}%",
                flush=True
            )

            time.sleep(0.1)

        print("=" * 45, flush=True)
        print("所有批次已经处理，开始检查数据库...", flush=True)

        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM `{TABLE_NAME}`"
            )
            database_total = cursor.fetchone()[0]

        print("=" * 45, flush=True)
        print("本次同步统计：", flush=True)
        print(f"新增影片：{total_inserted}", flush=True)
        print(f"更新影片：{total_updated}", flush=True)
        print(f"跳过影片：{total_skipped}", flush=True)
        print(f"本次处理记录：{processed}", flush=True)
        print(f"数据库当前总记录：{database_total}", flush=True)
        print("本次数据同步完成", flush=True)
        print("=" * 45, flush=True)

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        sync_data()

    except Exception as e:
        print("=" * 45, flush=True)
        print(f"同步失败：{type(e).__name__}: {e}", flush=True)
        print("完整错误位置：", flush=True)
        traceback.print_exc()
        print("=" * 45, flush=True)
        sys.exit(1)
