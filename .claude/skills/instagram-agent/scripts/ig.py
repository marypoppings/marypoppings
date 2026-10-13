#!/usr/bin/env python3
"""Multi-account Instagram Graph API client. Stdlib only. Writes are dry-run unless --yes."""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    path = os.environ.get("IG_ACCOUNTS_FILE", os.path.join(SKILL_DIR, "accounts.json"))
    if not os.path.exists(path):
        sys.exit(f"Missing {path}. Copy accounts.example.json to accounts.json and fill it in.")
    with open(path) as f:
        return json.load(f)


def pick(cfg, name, allow_all=False):
    accts = cfg["accounts"]
    if name == "all":
        if not allow_all:
            sys.exit("-a all is only allowed for read-only commands.")
        return accts
    for a in accts:
        if a["name"] == name:
            return [a]
    sys.exit(f"Unknown account '{name}'. Known: {', '.join(a['name'] for a in accts)}")


def call(cfg, acct, method, path, params=None):
    token = os.environ.get(acct["token_env"])
    if not token:
        sys.exit(f"Env var {acct['token_env']} is not set (needed for '{acct['name']}').")
    url = f"https://graph.facebook.com/{cfg.get('api_version', 'v23.0')}/{path}"
    data = dict(params or {})
    data["access_token"] = token
    enc = urllib.parse.urlencode(data)
    req = urllib.request.Request(url + "?" + enc) if method == "GET" else \
        urllib.request.Request(url, data=enc.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = json.loads(e.read() or b"{}").get("error", {})
        sys.exit(f"[{acct['name']}] API error {e.code}: {err.get('message', e.reason)}")


def dry(args, acct, what):
    print(f"[DRY RUN] account={acct['name']}: {what}\nRe-run with --yes to execute.")


def cmd_accounts(cfg, args):
    for a in pick(cfg, "all", True):
        ok = "token set" if os.environ.get(a["token_env"]) else f"MISSING {a['token_env']}"
        print(f"{a['name']:<20} {a['ig_user_id']:<20} {ok}")


def cmd_media(cfg, args):
    for a in pick(cfg, args.account, True):
        r = call(cfg, a, "GET", f"{a['ig_user_id']}/media", {
            "fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count",
            "limit": args.limit})
        print(f"== {a['name']} ==")
        for m in r.get("data", []):
            cap = (m.get("caption") or "").replace("\n", " ")[:70]
            print(f"{m['id']} {m['timestamp'][:10]} {m['media_type']:<14} "
                  f"♥{m.get('like_count', 0)} 💬{m.get('comments_count', 0)} {cap}\n  {m['permalink']}")


def cmd_insights(cfg, args):
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=args.days)
    for a in pick(cfg, args.account, True):
        print(f"== {a['name']} (last {args.days}d) ==")
        r = call(cfg, a, "GET", f"{a['ig_user_id']}/insights", {
            "metric": "reach,views,accounts_engaged,total_interactions,follows_and_unfollows",
            "period": "day", "metric_type": "total_value",
            "since": int(since.timestamp()), "until": int(until.timestamp())})
        for m in r.get("data", []):
            print(f"{m['name']:<22} {m.get('total_value', {}).get('value')}")


def cmd_hashtag(cfg, args):
    """Read-only: top posts for a hashtag, to find engagement targets (limit: 30 unique hashtags / 7 days)."""
    a = pick(cfg, args.account)[0]
    hid = call(cfg, a, "GET", "ig_hashtag_search",
               {"user_id": a["ig_user_id"], "q": args.tag.lstrip("#")})["data"][0]["id"]
    r = call(cfg, a, "GET", f"{hid}/top_media", {
        "user_id": a["ig_user_id"], "limit": args.limit,
        "fields": "id,caption,media_type,permalink,like_count,comments_count,timestamp"})
    for m in r.get("data", []):
        cap = (m.get("caption") or "").replace("\n", " ")[:90]
        print(f"{m['timestamp'][:10]} ♥{m.get('like_count', 0)} 💬{m.get('comments_count', 0)} {m['permalink']}\n  {cap}")


def cmd_comments(cfg, args):
    a = pick(cfg, args.account)[0]
    r = call(cfg, a, "GET", f"{args.media_id}/comments",
             {"fields": "id,text,username,timestamp,hidden", "limit": 50})
    for c in r.get("data", []):
        print(f"{c['id']} @{c['username']} {c['timestamp'][:10]}{' [hidden]' if c.get('hidden') else ''}: {c['text']}")


def cmd_reply(cfg, args):
    a = pick(cfg, args.account)[0]
    if not args.yes:
        return dry(args, a, f"reply to comment {args.comment_id}: {args.text!r}")
    print(call(cfg, a, "POST", f"{args.comment_id}/replies", {"message": args.text}))


def cmd_hide(cfg, args):
    a = pick(cfg, args.account)[0]
    if not args.yes:
        return dry(args, a, f"hide comment {args.comment_id}")
    print(call(cfg, a, "POST", args.comment_id, {"hide": "true"}))


def cmd_publish(cfg, args):
    a = pick(cfg, args.account)[0]
    kind = "REELS" if args.video_url else "IMAGE"
    if not args.yes:
        return dry(args, a, f"publish {kind} {args.video_url or args.image_url}\ncaption: {args.caption}")
    params = {"caption": args.caption}
    if args.video_url:
        params.update(media_type="REELS", video_url=args.video_url)
    else:
        params["image_url"] = args.image_url
    cid = call(cfg, a, "POST", f"{a['ig_user_id']}/media", params)["id"]
    for _ in range(60):
        status = call(cfg, a, "GET", cid, {"fields": "status_code"}).get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"[{a['name']}] container {status}")
        time.sleep(5)
    else:
        sys.exit(f"[{a['name']}] timed out waiting for media processing (container {cid})")
    mid = call(cfg, a, "POST", f"{a['ig_user_id']}/media_publish", {"creation_id": cid})["id"]
    link = call(cfg, a, "GET", mid, {"fields": "permalink"}).get("permalink")
    print(f"Published on {a['name']}: {link}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, acct=True):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        if acct:
            s.add_argument("-a", "--account", required=True)
        return s

    add("accounts", cmd_accounts, acct=False)
    add("media", cmd_media).add_argument("--limit", type=int, default=10)
    add("insights", cmd_insights).add_argument("--days", type=int, default=28)
    s = add("hashtag", cmd_hashtag)
    s.add_argument("--tag", required=True); s.add_argument("--limit", type=int, default=15)
    add("comments", cmd_comments).add_argument("--media-id", required=True)
    s = add("reply", cmd_reply)
    s.add_argument("--comment-id", required=True); s.add_argument("--text", required=True)
    s.add_argument("--yes", action="store_true")
    s = add("hide", cmd_hide)
    s.add_argument("--comment-id", required=True); s.add_argument("--yes", action="store_true")
    s = add("publish", cmd_publish)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--image-url"); g.add_argument("--video-url")
    s.add_argument("--caption", required=True); s.add_argument("--yes", action="store_true")

    args = p.parse_args()
    args.fn(load_config(), args)


if __name__ == "__main__":
    main()
