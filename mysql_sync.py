
import os
import json
import sys
import pymysql
import time

JSON_FILE = "wsyzy_vod.json"
TABLE_NAME = "wsyzy_vod"
BATCH_SIZE = 20

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
    return "" if value is None else str(value)


def load_json():
    with open(JSON_FILE, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON 顶层必须是数组")

    records = {}

    for item in data:
        if isinstance(item, dict) and item.get("id"):
            records[str(item["id"])] = item

    return list(records.values()), len(data)


def create_table(cursor):
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS `wsyzy_vod` (
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
    """)


def connect_db():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        connect_timeout=30,
        read_timeout=180,
        write_timeout=180,
        autocommit=False
    )


def sync_data():
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("MySQL 环境变量配置不完整")

    records, original_count = load_json()

    print(f"JSON 原始记录：{original_count}", flush=True)
    print(f"有效记录：{len(records)}", flush=True)
    print(f"无效记录：{original_count - len(records)}", flush=True)

    if not records:
        print("没有需要同步的数据")
        return

    connection = connect_db()

    inserted = 0
    updated = 0
    failed = 0
    total_batches = (len(records) + BATCH_SIZE - 1) // BATCH_SIZE

    try:
        with connection.cursor() as cursor:
            print("正在创建或检查数据表...", flush=True)
            create_table(cursor)
            connection.commit()

            print("数据表检查完成，开始分批同步...", flush=True)

            columns = ",".join(f"`{x}`" for x in FIELDS)
            placeholders = ",".join(["%s"] * len(FIELDS))
            update_fields = [x for x in FIELDS if x != "id"]

            update_sql = ",".join(
                f"`{x}`=VALUES(`{x}`)" for x in update_fields
            )

            sql = f"""
                INSERT INTO `{TABLE_NAME}` ({columns})
                VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {update_sql}
            """

            for batch_no, start in enumerate(
                range(0, len(records), BATCH_SIZE), 1
            ):
                batch = records[start:start + BATCH_SIZE]

                try:
                    connection.ping(reconnect=True)

                    ids = [str(x["id"]) for x in batch]
                    marks = ",".join(["%s"] * len(ids))

                    cursor.execute(
                        f"SELECT `id` FROM `{TABLE_NAME}` "
                        f"WHERE `id` IN ({marks})",
                        ids
                    )

                    existing = {str(row[0]) for row in cursor.fetchall()}

                    rows = [
                        tuple(val(item, field) for field in FIELDS)
                        for item in batch
                    ]

                    cursor.executemany(sql, rows)

                    new_count = sum(
                        1 for x in ids if x not in existing
                    )

                    inserted += new_count
                    updated += len(batch) - new_count

                    # 每批独立提交，避免长事务
                    connection.commit()

                    if batch_no % 10 == 0 or batch_no == total_batches:
                        print(
                            f"进度：{batch_no}/{total_batches} 批，"
                            f"已处理 {min(start + len(batch), len(records))}"
                            f"/{len(records)} 条，"
                            f"新增 {inserted}，更新 {updated}",
                            flush=True
                        )

                    time.sleep(0.1)

                except Exception:
                    connection.rollback()
                    failed += len(batch)
                    print(
                        f"第 {batch_no} 批同步失败，"
                        f"记录ID：{[x['id'] for x in batch]}",
                        flush=True
                    )
                    raise

        # 分批核验，不一次性构造上万个 ID 查询条件
        print("开始核验数据库...", flush=True)

        verified = 0

        with connection.cursor() as cursor:
            for start in range(0, len(records), BATCH_SIZE):
                batch_ids = [
                    str(x["id"])
                    for x in records[start:start + BATCH_SIZE]
                ]

                marks = ",".join(["%s"] * len(batch_ids))

                cursor.execute(
                    f"SELECT COUNT(*) FROM `{TABLE_NAME}` "
                    f"WHERE `id` IN ({marks})",
                    batch_ids
                )

                verified += cursor.fetchone()[0]

            cursor.execute(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")
            database_total = cursor.fetchone()[0]

        print("=" * 40)
        print("同步完成")
        print(f"原始记录：{original_count}")
        print(f"新增记录：{inserted}")
        print(f"更新记录：{updated}")
        print(f"失败记录：{failed}")
        print(f"数据库核验：{verified}/{len(records)}")
        print(f"数据库总记录：{database_total}")
        print("=" * 40)

        if verified != len(records):
            raise RuntimeError("数据库核验数量不一致")

    finally:
        connection.close()


if __name__ == "__main__":
    try:
        sync_data()
    except Exception as e:
        print(f"同步失败：{type(e).__name__}: {e}", flush=True)
        sys.exit(1)
