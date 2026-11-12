# deploy/

ინსტალერები production `ris` სერვერისთვის. ამ ბრანჩში (`deploy`) იდება ყველაფერი,
რაც სერვერზე უნდა გაეშვას; სერვერი თავად ჩამოტვირთავს.

## ერთჯერადი დაყენება (ris)

```bash
curl -fsSL https://raw.githubusercontent.com/giojik/innova-pacs/deploy/deploy/pacs-deploy.sh \
  -o /usr/local/bin/pacs-deploy && chmod +x /usr/local/bin/pacs-deploy
```

## გამოყენება

```bash
pacs-deploy list              # რა არის ხელმისაწვდომი
pacs-deploy <name>            # აჩვენებს აღწერას, ითხოვს დადასტურებას, უშვებს
pacs-deploy self-update
```

- ფაილი იწერება ზუსტი commit-ით (`api.github.com` → sha), ამიტომ GitHub raw-ის ქეში არ ერევა.
- ჩამოტვირთული ასლები: `/opt/pacs/deploy-cache/`, ლოგი: `/var/log/pacs-deploy.log`.
- ყველა ინსტალერი თავად აკეთებს ბექაპს (`*.bak-<დრო>`) და განმეორებით გაშვებისას არაფერს აორმაგებს.
- რეპო საჯაროა — ინსტალერებში პაროლი/საიდუმლო არასდროს იწერება (ყველაფერი `.env`-დან).
