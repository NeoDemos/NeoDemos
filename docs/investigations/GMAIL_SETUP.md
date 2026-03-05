# Gmail SMTP Configuration for NeoDemos Error Notifications

## Setup Steps

1. **Enable 2-Factor Authentication on your Gmail account**
   - Go to https://myaccount.google.com/
   - Click "Security" in the left menu
   - Enable "2-Step Verification"

2. **Generate an App Password**
   - Go to https://myaccount.google.com/apppasswords
   - Select "Mail" and "Mac" (or your platform)
   - Google will generate a 16-character password
   - Copy this password

3. **Configure .env file**
   ```bash
   SMTP_USER=your-gmail@gmail.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx  # 16-char app password (remove spaces in .env)
   ERROR_EMAIL_TO=tak.dpa@gmail.com
   ```

4. **Test Email Sending (optional)**
   ```bash
   python -c "
   import asyncio
   from services.email_service import EmailService
   from datetime import datetime
   
   async def test():
       email = EmailService()
       await email.send_error_notification(
           'Test Error',
           'This is a test email',
           datetime.now()
       )
       print('✓ Email sent successfully')
   
   asyncio.run(test())
   "
   ```

## Troubleshooting

- **"Login failed"**: Check that your SMTP_USER and SMTP_PASSWORD are correct (no spaces in password)
- **"SMTP connection timeout"**: Verify Gmail hasn't blocked the login (check your Gmail Security page)
- **"SMTP auth failed"**: Ensure you've generated an App Password, not using your main Gmail password

## Note

- App Passwords only work if you have 2-Step Verification enabled
- Store credentials in `.env` file (never commit to git)
- Emails are only sent on refresh errors, not on success
