
import os
import sys
import json
import time
import traceback
import requests
import pymysql
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta


# ============================================================
# 基本配置
# ============================================================

API_URL = (
    "https://api.wsyzy.net/"
    "api.php/provide/vod/from/wsym3u8/at/xml"
)

JSON_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "wsyzy_vod.json"
)

TABLE_NAME = "wsyzy_vod"

# 采集最近48小时
HOURS = 48

# 接口每页数量（如果接口支持该参数）
PAGE_SIZE = 100

# 数据库每批20条
BATCH_SIZE = 20

# 每批最多重试3次
MAX_RETRIES = 3

# WSYZY 的 vod_time 如果使用北京时间，保持默认8
# 如果接口时间实际是UTC，请设置为0
SOURCE_TIME_OFFSET_HOURS = int(
    os.environ.get("SOURCE_TIME_OFFSET_HOURS", "8")
)


# ============================================================
# MySQL 环境变量
# ============================================================

DB_HOST = os.environ.get("MYSQL_HOST")
DB_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
DB_USER = os.environ.get("MYSQL_USER")
DB_PASSWORD = os.environ.get("MYSQL_PASSWORD")
DB_NAME = os.environ.get(
    "MYSQL_DATABASE",
    "mo5_dj5yx_com"
)


# ============================================================
# 数据库字段
# ============================================================

FIELDS = [
    "last",
    "id",
    "tid",
    "name",
    "type",
    "pic",
    "lang",
    "area",
    "year",
    "state",
    "note",
    "actor",
    "director",
    "dl",
    "des"
]


# ============================================================
# 通用函数
# ============================================================

def val(item, key):
    value = item.get(key)

    if value is None:
        return ""

    return str(value)


def xml_text(item, field):
    node = item.find(field)

    if node is None or node.text is None:
        return ""

    return node.text.strip()


def parse_time(value):
    if not value:
        return None

    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


# ============================================================
# XML 解析
# ============================================================

def parse_video(item):
    vod_id = xml_text(item, "vod_id")

    if not vod_id:
        return None

    vod_time = xml_text(item, "vod_time")

    # 播放源
    play_from = xml_text(item, "vod_play_from")

    # 播放地址和集数
    play_url = xml_text(item, "vod_play_url")

    # 苹果CMS播放信息格式
    dl = ""

    if play_from or play_url:
        dl = play_from + "$$$" + play_url

    return {
        "last": vod_time,
        "id": vod_id,
        "tid": xml_text(item, "type_id"),
        "name": xml_text(item, "vod_name"),
        "type": xml_text(item, "type_name"),
        "pic": xml_text(item, "vod_pic"),
        "lang": xml_text(item, "vod_lang"),
        "area": xml_text(item, "vod_area"),
        "year": xml_text(item, "vod_year"),
        "state": xml_text(item, "vod_state"),
        "note": xml_text(item, "vod_remarks"),
        "actor": xml_text(item, "vod_actor"),
        "director": xml_text(item, "vod_director"),
        "dl": dl,
        "des": xml_text(item, "vod_content")
    }


# ============================================================
# 下载 XML 数据
# ============================================================

def fetch_page(session, page):
    params = {
        "ac": "videolist",
        "pg": page,
        "h": HOURS,
        "pagesize": PAGE_SIZE
    }

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            response = session.get(
                API_URL,
                params=params,
                timeout=60
            )

            response.raise_for_status()

            # XML 编码由 XML 声明或 requests 推断
            response.encoding = response.apparent_encoding

            root = ET.fromstring(response.content)

            videos = []

            for node in root.iter():
                if node.tag.split("}")[-1] == "video":
                    video = parse_video(node)

                    if video:
                        videos.append(video)

            # 获取总页数
            page_count = 1

            for node in root.iter():
                if node.tag.split("}")[-1] == "list":
                    page_count = int(
                        node.attrib.get("pagecount", "1") or "1"
                    )
                    break

            return videos, page_count

        except Exception as e:
            print(
                f"下载第 {page} 页失败，"
                f"重试 {attempt}/{MAX_RETRIES}：{e}",
                flush=True
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 3)

    raise RuntimeError(
        f"下载第 {page} 页连续失败，停止采集"
    )


# ============================================================
# 采集最近48小时影片
# ============================================================

def fetch_recent_videos():

    print("=" * 50, flush=True)
    print("开始下载 WSYZY 最近48小时数据", flush=True)
    print("=" * 50, flush=True)

    # GitHub Actions 通常使用UTC时间。
    # 根据数据源时间偏移转换成数据源本地时间。
    source_now = (
        datetime.utcnow()
        + timedelta(hours=SOURCE_TIME_OFFSET_HOURS)
    )

    start_time = source_now - timedelta(hours=HOURS)

    print(
        f"数据源当前时间：{source_now}",
        flush=True
    )

    print(
        f"采集起始时间：{start_time}",
        flush=True
    )

    session = requests.Session()

    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })

    all_videos = {}

    try:
        page = 1
        page_count = 1

        while page <= page_count:

            print(
                f"正在下载第 {page}/{page_count} 页...",
                flush=True
            )

            videos, page_count = fetch_page(
                session,
                page
            )

            print(
                f"第 {page} 页获取 {len(videos)} 条",
                flush=True
            )

            for video in videos:

                vod_time = parse_time(video["last"])

                if vod_time is None:
                    continue

                # 严格过滤最近48小时
                if not (start_time <= vod_time <= source_now):
                    continue

                # 相同ID去重
                all_videos[str(video["id"])] = video

            print(
                f"目前符合48小时条件：{len(all_videos)} 条",
                flush=True
            )

            page += 1
            time.sleep(0.3)

    finally:
        session.close()

    result = list(all_videos.values())

    print("=" * 50, flush=True)
    print(
        f"最近48小时采集完成，共 {len(result)} 部影片",
        flush=True
    )
    print("=" * 50, flush=True)

    return result


# ============================================================
# 保存 JSON 文件
# ============================================================

def save_json(records):

    print("正在生成 JSON 文件...", flush=True)

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            records,
            f,
            ensure_ascii=False,
            indent=2
        )

    file_size = os.path.getsize(JSON_FILE)

    print(
        f"JSON生成成功：{JSON_FILE}",
        flush=True
    )

    print(
        f"JSON记录数量：{len(records)}",
        flush=True
    )

    print(
        f"JSON文件大小：{file_size / 1024:.2f} KB",
        flush=True
    )


# ============================================================
# MySQL连接
# ============================================================

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


# ============================================================
# 创建数据表
# ============================================================

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


# ============================================================
# 生成插入和更新SQL
# ============================================================

def make_sql():

    columns = ",".join(
        f"`{x}`" for x in FIELDS
    )

    placeholders = ",".join(
        ["%s"] * len(FIELDS)
    )

    update_fields = [
        x for x in FIELDS if x != "id"
    ]

    update_sql = ",".join(
        f"`{x}`=VALUES(`{x}`)"
        for x in update_fields
    )

    return f"""
        INSERT INTO `{TABLE_NAME}` ({columns})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {update_sql}
    """


# ============================================================
# 查询数据库已有记录
# ============================================================

def get_existing_records(conn, batch):

    ids = [
        str(item["id"])
        for item in batch
    ]

    if not ids:
        return {}

    placeholders = ",".join(
        ["%s"] * len(ids)
    )

    sql = f"""
        SELECT `id`, `last`, `dl`
        FROM `{TABLE_NAME}`
        WHERE `id` IN ({placeholders})
    """

    with conn.cursor(
        pymysql.cursors.DictCursor
    ) as cursor:

        cursor.execute(sql, ids)

        rows = cursor.fetchall()

    existing = {}

    for row in rows:

        existing[str(row["id"])] = {
            "last": (
                "" if row["last"] is None
                else str(row["last"]).strip()
            ),
            "dl": (
                "" if row["dl"] is None
                else str(row["dl"]).strip()
            )
        }

    return existing


# ============================================================
# 判断是否需要更新
# ============================================================

def check_changed(item, old):

    # 数据库不存在，新增
    if old is None:
        return "new"

    new_last = val(item, "last").strip()
    old_last = old["last"]

    new_dl = val(item, "dl").strip()
    old_dl = old["dl"]

    # 更新时间发生变化
    if new_last != old_last:
        return "updated"

    # 播放源、集数、播放地址发生变化
    if new_dl != old_dl:
        return "updated"

    # 两者都没变化
    return "skipped"


# ============================================================
# 同步一个批次
# ============================================================

def sync_batch(
    conn,
    sql,
    batch,
    batch_no,
    total_batches
):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            print(
                f"第 {batch_no}/{total_batches} 批开始，"
                f"共 {len(batch)} 条，"
                f"尝试 {attempt}/{MAX_RETRIES}",
                flush=True
            )

            conn.ping(reconnect=True)

            # 一次性查询本批次的数据库记录
            existing = get_existing_records(
                conn,
                batch
            )

            new_records = []
            update_records = []
            skipped_records = []

            for item in batch:

                video_id = str(item["id"])

                old = existing.get(video_id)

                status = check_changed(
                    item,
                    old
                )

                if status == "new":
                    new_records.append(item)

                elif status == "updated":
                    update_records.append(item)

                else:
                    skipped_records.append(item)

            changed_records = (
                new_records + update_records
            )

            if not changed_records:

                print(
                    f"第 {batch_no} 批没有变化，"
                    f"跳过 {len(skipped_records)} 条",
                    flush=True
                )

                return (
                    conn,
                    len(new_records),
                    len(update_records),
                    len(skipped_records)
                )

            rows = [
                tuple(
                    val(item, field)
                    for field in FIELDS
                )
                for item in changed_records
            ]

            with conn.cursor() as cursor:

                cursor.executemany(
                    sql,
                    rows
                )

            conn.commit()

            print(
                f"第 {batch_no} 批提交成功："
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
                f"等待 {wait_seconds} 秒后重新连接...",
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


# ============================================================
# MySQL数据同步
# ============================================================

def sync_data(records):

    if not all([
        DB_HOST,
        DB_USER,
        DB_PASSWORD,
        DB_NAME
    ]):

        raise ValueError(
            "MySQL环境变量配置不完整"
        )

    print("=" * 50, flush=True)
    print("开始MySQL数据同步", flush=True)
    print(f"待处理影片：{len(records)}", flush=True)
    print("=" * 50, flush=True)

    if not records:

        print(
            "最近48小时没有符合条件的影片，无需同步",
            flush=True
        )

        return

    conn = None

    try:

        conn = connect_db()

        create_table(conn)

        sql = make_sql()

        total_batches = (
            len(records) + BATCH_SIZE - 1
        ) // BATCH_SIZE

        total_inserted = 0
        total_updated = 0
        total_skipped = 0
        processed = 0

        print("开始分批同步...", flush=True)

        for batch_no, start in enumerate(
            range(0, len(records), BATCH_SIZE),
            1
        ):

            batch = records[
                start:start + BATCH_SIZE
            ]

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
                f"完成比例："
                f"{processed / len(records) * 100:.2f}%",
                flush=True
            )

            time.sleep(0.1)

        print("=" * 50, flush=True)
        print("所有批次处理完成，检查数据库...", flush=True)

        with conn.cursor() as cursor:

            cursor.execute(
                f"SELECT COUNT(*) FROM `{TABLE_NAME}`"
            )

            database_total = cursor.fetchone()[0]

        print("=" * 50, flush=True)
        print("本次同步统计：", flush=True)
        print(f"新增影片：{total_inserted}", flush=True)
        print(f"更新影片：{total_updated}", flush=True)
        print(f"跳过影片：{total_skipped}", flush=True)
        print(f"本次处理：{processed}", flush=True)
        print(f"数据库总记录：{database_total}", flush=True)
        print("MySQL同步成功", flush=True)
        print("=" * 50, flush=True)

    finally:

        if conn:

            try:
                conn.close()
            except Exception:
                pass


# ============================================================
# 主程序：下载 -> JSON -> MySQL
# ============================================================

def main():

    print("=" * 55, flush=True)
    print("WSYZY最近48小时影视数据同步任务启动", flush=True)
    print("=" * 55, flush=True)

    # 第一步：下载并筛选最近48小时影片
    records = fetch_recent_videos()

    # 第二步：生成JSON文件
    save_json(records)

    # 第三步：读取JSON，确认文件生成成功
    if not os.path.exists(JSON_FILE):
        raise FileNotFoundError(
            f"JSON文件生成失败：{JSON_FILE}"
        )

    with open(
        JSON_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        json_records = json.load(f)

    if not isinstance(json_records, list):
        raise ValueError(
            "JSON顶层必须是数组"
        )

    print(
        f"JSON文件读取成功，共 {len(json_records)} 条",
        flush=True
    )

    # 第四步：对比数据库并同步
    sync_data(json_records)

    print("=" * 55, flush=True)
    print("全部任务执行成功！", flush=True)
    print("=" * 55, flush=True)


if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("=" * 55, flush=True)

        print(
            f"任务失败：{type(e).__name__}: {e}",
            flush=True
        )

        print("完整错误位置：", flush=True)

        traceback.print_exc()

        print("=" * 55, flush=True)

        sys.exit(1)
