# -*- coding: utf-8 -*-
import s3lite, collections, datetime
objs = s3lite.list_objects("rec/")
percam = collections.defaultdict(list)
total = 0
for o in objs:
    k = o["key"]
    if not k.endswith(".mp4"):
        continue
    p = k.split("/")
    if len(p) < 3:
        continue
    percam[p[1]].append((p[-1], o["size"], o.get("modified", "")))
    total += o["size"]
print("TOTAL segments=%d sizeGB=%.2f" % (sum(len(v) for v in percam.values()), total / 1e9))
now = datetime.datetime.utcnow()
for cam in sorted(percam):
    items = sorted(percam[cam])
    sz = sum(i[1] for i in items)
    days = collections.Counter(i[0][:10] for i in items)
    last = items[-1]
    # возраст последнего сегмента (по modified)
    age = "?"
    try:
        t = datetime.datetime.strptime(last[2][:19], "%Y-%m-%dT%H:%M:%S")
        age = "%.1f мин назад" % ((now - t).total_seconds() / 60)
    except Exception:
        pass
    print("== %s: %d сегм, %.2f ГБ | дни: %s" % (cam, len(items), sz / 1e9, dict(days)))
    print("   первый=%s  последний=%s (загружен %s)" % (items[0][0], last[0], age))
