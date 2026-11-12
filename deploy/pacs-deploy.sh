#!/bin/bash
# pacs-deploy — GitHub-ის deploy ბრანჩიდან ინსტალერის ჩამოტვირთვა და გაშვება.
#   pacs-deploy list            — ხელმისაწვდომი ინსტალერები
#   pacs-deploy <name>          — ჩამოტვირთვა, დადასტურება, გაშვება
#   pacs-deploy <name> -y       — დადასტურების გარეშე
#   pacs-deploy self-update     — თავად ამ ბრძანების განახლება
set -euo pipefail
REPO="giojik/innova-pacs"; BRANCH="deploy"; LOG="/var/log/pacs-deploy.log"
WORK="/opt/pacs/deploy-cache"; mkdir -p "$WORK"

sha() {   # ბრანჩის ზუსტი commit — raw.githubusercontent-ის 5-წუთიანი ქეშის გვერდის ავლით
  curl -fsSL "https://api.github.com/repos/$REPO/commits/$BRANCH" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])"
}
raw() { curl -fsSL "https://raw.githubusercontent.com/$REPO/$1/$2"; }

cmd="${1:-list}"
case "$cmd" in
  list)
    S=$(sha); echo "deploy@${S:0:7}"
    curl -fsSL "https://api.github.com/repos/$REPO/contents/deploy?ref=$S" | python3 -c "
import sys, json
for f in json.load(sys.stdin):
    n = f['name']
    if n.endswith('.sh') and n != 'pacs-deploy.sh': print('  ', n[:-3])"
    ;;
  self-update)
    S=$(sha); raw "$S" deploy/pacs-deploy.sh > /tmp/pacs-deploy.new
    bash -n /tmp/pacs-deploy.new && install -m 755 /tmp/pacs-deploy.new /usr/local/bin/pacs-deploy
    echo "✓ pacs-deploy განახლდა (${S:0:7})"
    ;;
  *)
    S=$(sha); F="$WORK/${cmd}-${S:0:7}.sh"
    raw "$S" "deploy/${cmd}.sh" > "$F" || { echo "✗ deploy/${cmd}.sh ვერ მოიძებნა — იხ. pacs-deploy list"; exit 1; }
    bash -n "$F"
    echo "── $cmd @ ${S:0:7} ──"; grep -m3 '^# ' "$F" | sed 's/^# /   /'
    if [ "${2:-}" != "-y" ]; then read -r -p "გავუშვა? [y/N] " a; [ "$a" = "y" ] || { echo "გაუქმდა"; exit 0; }; fi
    echo "$(date '+%F %T') START $cmd ${S:0:7}" >> "$LOG"
    if bash "$F"; then echo "$(date '+%F %T') OK    $cmd ${S:0:7}" >> "$LOG"
    else rc=$?; echo "$(date '+%F %T') FAIL  $cmd ${S:0:7} rc=$rc" >> "$LOG"; exit $rc; fi
    ;;
esac
