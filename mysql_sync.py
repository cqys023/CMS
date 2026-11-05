
import os
import json
import sys
import pymysql

# =========================
# 配置
# =========================

JSON_FILE = "wsyzy_vod.json"

DB_HOST = os.environ.get("MYSQL_HOST")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_USER = os.environ.get("MYSQL_USER")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD")
DB_NAME = os.environ.get("MYSQL_DATABASE", "game_html")

TABLE_NAME = "wsyzy_vod"

FIELDS = [
    "last", "id", "tid", "name", "type", "pic",
    "lang", "area", "year", "state", "note",
    "actor", "director", "dl", "des"
]


def value(item, key):
    v = item.get(key)
    return "" if v is None else str(v)


def load_json():
    if not os.path.exists(JSON_FILE):
        raise FileNotFoundError(f"找不到文件：{JSON_FILE}")

    with open(JSON_FILE, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON 顶层必须是数组")

    # 按 id 去重，同一个 id 以最后一条数据为准
    records = {}
    skipped = 0

    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            skipped += 1
            continue

        records[str(item["id"])] = item

    return list(records.values()), skipped, len(data)


def create_table(cursor):
    sql = """
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
    """
    cursor.execute(sql)


def sync_data():
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("MySQL 环境变量配置不完整")

    records, skipped, original_count = load_json()

    print(f"JSON 原始记录数：{original_count}")
    print(f"有效去重记录数：{len(records)}")
    print(f"跳过无效记录数：{skipped}")

    connection = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        connect_timeout=20,
        read_timeout=60,
        write_timeout=60,
        autocommit=False
    )

    inserted = 0
    updated = 0

    try:
        with connection.cursor() as cursor:
            create_table(cursor)

            if not records:
                connection.commit()
                print("没有有效数据，无需同步")
                return

            ids = [str(item["id"]) for item in records]

            # 查询数据库中已经存在的 ID
            existing_ids = set()

            for start in range(0, len(ids), 500):
                batch_ids = ids[start:start + 500]
                placeholders = ",".join(["%s"] * len(batch_ids))

                cursor.execute(
                    f"SELECT `id` FROM `{TABLE_NAME}` "
                    f"WHERE `id` IN ({placeholders})",
                    batch_ids
                )

                existing_ids.update(row[0] for row in cursor.fetchall())

            # 构造批量插入 SQL
            columns = ",".join(f"`{field}`" for field in FIELDS)
            placeholders = ",".join(["%s"] * len(FIELDS))

            update_fields = [
                field for field in FIELDS if field != "id"
            ]

            update_sql = ",".join(
                f"`{field}`=VALUES(`{field}`)"
                for field in update_fields
            )

            sql = f"""
                INSERT INTO `{TABLE_NAME}` ({columns})
                VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {update_sql}
            """

            rows = [
                tuple(value(item, field) for field in FIELDS)
                for item in records
            ]

            cursor.executemany(sql, rows)

            inserted = sum(
                1 for item in records
                if str(item["id"]) not in existing_ids
            )

            updated = len(records) - inserted

        connection.commit()

        # 再次核验数据库记录
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM `{TABLE_NAME}`"
            )
            total = cursor.fetchone()[0]

            cursor.execute(
                f"SELECT COUNT(*) FROM `{TABLE_NAME}` "
                f"WHERE `id` IN ({','.join(['%s'] * len(ids))})",
                ids
            )
            verified = cursor.fetchone()[0]

        print("=" * 40)
        print("MySQL 同步完成")
        print(f"原始 JSON 记录：{original_count}")
        print(f"新增记录：{inserted}")
        print(f"更新记录：{updated}")
        print(f"跳过记录：{skipped}")
        print(f"失败记录：0")
        print(f"本次数据核验：{verified}/{len(records)}")
        print(f"数据库总记录数：{total}")
        print("=" * 40)

        if verified != len(records):
            raise RuntimeError("数据库记录核验不一致")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    try:
        sync_data()
    except Exception as e:
        print(f"同步失败：{type(e).__name__}: {e}")
        sys.exit(1)
