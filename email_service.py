import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RESPONSABLES_EMAILS, TOURNOI_NOM


def send_registration_email(team_data: dict, players: list):
    """Envoie un email récapitulatif aux responsables quand une équipe s'inscrit."""
    subject = f"[{TOURNOI_NOM}] Nouvelle inscription : {team_data['name']}"

    players_html = "".join([
        f"<tr><td>{i+1}</td><td>{p}</td></tr>"
        for i, p in enumerate(players)
    ])

    body_html = f"""
    <html><body>
    <h2>Nouvelle inscription au tournoi</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><td><strong>Équipe</strong></td><td>{team_data['name']}</td></tr>
        <tr><td><strong>Capitaine</strong></td><td>{team_data['captain_name']}</td></tr>
        <tr><td><strong>Téléphone</strong></td><td>{team_data['phone']}</td></tr>
        <tr><td><strong>Date d'inscription</strong></td><td>{team_data.get('created_at', '')}</td></tr>
    </table>
    <h3>Joueurs ({len(players)})</h3>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>#</th><th>Nom</th></tr>
        {players_html}
    </table>
    <p>Connectez-vous à l'interface admin pour valider cette inscription.</p>
    </body></html>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = ", ".join(RESPONSABLES_EMAILS)
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, RESPONSABLES_EMAILS, msg.as_string())

        print(f"Email envoyé pour l'équipe {team_data['name']}")
        return True
    except Exception as e:
        print(f"Erreur envoi email : {e}")
        return False
