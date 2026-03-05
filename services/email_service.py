"""
Email service for NeoDemos error notifications
"""

import smtplib
import logging
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class EmailService:
    """Sends email notifications for refresh errors"""
    
    def __init__(self):
        """Initialize email service with SMTP configuration"""
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender_email = os.getenv("SMTP_USER", "")
        self.sender_password = os.getenv("SMTP_PASSWORD", "")
        self.recipient_email = os.getenv("ERROR_EMAIL_TO", "tak.dpa@gmail.com")
        
        if not self.sender_email or not self.sender_password:
            logger.warning("SMTP credentials not configured. Email notifications disabled.")
    
    async def send_error_notification(self, subject: str, error_message: str, timestamp: datetime):
        """
        Send error notification email
        
        Args:
            subject: Email subject
            error_message: The error message to send
            timestamp: When the error occurred
        """
        if not self.sender_email or not self.sender_password:
            logger.warning("Email service not configured. Skipping notification.")
            return
        
        try:
            # Create email message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[NeoDemos] {subject}"
            msg["From"] = self.sender_email
            msg["To"] = self.recipient_email
            
            # Create plain text and HTML versions
            text = f"""NeoDemos Refresh Error

Time: {timestamp.isoformat()}
Subject: {subject}

Error Details:
{error_message}

---
NeoDemos Monitoring System
"""
            
            html = f"""
            <html>
                <body style="font-family: Arial, sans-serif; color: #333;">
                    <h2 style="color: #d32f2f;">NeoDemos Refresh Error</h2>
                    <p><strong>Time:</strong> {timestamp.isoformat()}</p>
                    <p><strong>Subject:</strong> {subject}</p>
                    
                    <h3 style="color: #d32f2f;">Error Details:</h3>
                    <pre style="background-color: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto;">
{error_message}
                    </pre>
                    
                    <hr style="margin-top: 20px; border: none; border-top: 1px solid #ddd;">
                    <p style="color: #999; font-size: 12px;">NeoDemos Monitoring System</p>
                </body>
            </html>
            """
            
            # Attach parts
            part1 = MIMEText(text, "plain")
            part2 = MIMEText(html, "html")
            msg.attach(part1)
            msg.attach(part2)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
            
            logger.info(f"Error notification sent to {self.recipient_email}")
            
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")
