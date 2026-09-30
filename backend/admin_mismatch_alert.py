"""Send mismatch notices exclusively to the confirmed private admin chat."""
import json
import os
from pathlib import Path
import requests

def telegram_call(token, method, **parameters):
    response = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             json=parameters, timeout=30)
    payload = response.json()
    if not payload.get("ok"):
        # Do not include the request URL (which contains the bot token).
        raise RuntimeError(f"Telegram {method}: {payload.get('description', 'request failed')}")
    return payload.get("result")

def resolve_private_admin(call, configured_id="", username="rdgyan", channel="@mptendersalert"):
    configured_id = configured_id.strip()
    if configured_id:
        if not configured_id.isdigit() or int(configured_id) <= 0:
            raise ValueError("TELEGRAM_ADMIN_CHAT_ID must be a positive private user ID")
        chat_id = int(configured_id)
    else:
        # Private user handles are not valid Bot API sendMessage destinations.
        # Resolve the exact requested handle through the project's channel roster.
        members = call("getChatAdministrators", chat_id=channel) or []
        matches = {int(member["user"]["id"]) for member in members
                   if str(member.get("user", {}).get("username", "")).casefold() == username.casefold()
                   and not member.get("user", {}).get("is_bot")}
        if len(matches) != 1:
            raise ValueError(f"Could not uniquely resolve private admin @{username}")
        chat_id = matches.pop()
    chat = call("getChat", chat_id=chat_id)
    if chat.get("type") != "private" or int(chat.get("id", 0)) != chat_id:
        raise ValueError("Mismatch alert destination is not the confirmed private chat")
    if not configured_id and str(chat.get("username", "")).casefold() != username.casefold():
        raise ValueError("Private admin identity did not match the requested Telegram handle")
    return chat_id

def send_alert(report, call, configured_id=""):
    if report.get("status") == "verified":
        return {"status": "not_needed"}
    chat_id = resolve_private_admin(call, configured_id)
    mismatches = report.get("organisation_mismatches") or []
    lines = ["MP Tenders: डेटा में मिसमैच", "",
             f"Portal Count: {report.get('portal_tender_count', '—')}",
             f"Copied Count: {report.get('copied_unique_tender_ids', '—')}",
             f"Mismatch Organisations: {len(mismatches)}", ""]
    for item in mismatches[:30]:
        lines.append(f"{item.get('Organisation Name', '—')}: "
                     f"portal {item.get('Portal Count', '—')} / copied {item.get('Copied Count', '—')}")
    lines += ["", "https://tenders.codinglms.xyz/org/", "https://tenders.codinglms.xyz/data/"]
    call("sendMessage", chat_id=chat_id, text="\n".join(lines)[:4000], disable_web_page_preview=True)
    return {"status": "sent", "destination": "private_admin"}

def main():
    root = Path(__file__).resolve().parents[1]
    status = {"status": "not_needed"}
    report_file = root / "data/run_snapshot.json"
    try:
        if report_file.exists():
            report = json.loads(report_file.read_text())
            token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            if report.get("status") != "verified" and not token:
                raise ValueError("TELEGRAM_BOT_TOKEN is missing")
            status = send_alert(report, lambda method, **params: telegram_call(token, method, **params),
                                os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""))
    except Exception as exc:
        status = {"status": "blocked", "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
        print("::warning::Private admin mismatch alert blocked; see data/admin_mismatch_alert_status.json")
    (root / "data/admin_mismatch_alert_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
    print(json.dumps(status, ensure_ascii=False))

if __name__ == "__main__":
    main()
