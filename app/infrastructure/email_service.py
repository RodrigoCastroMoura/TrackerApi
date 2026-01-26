import os
from flask_mail import Mail, Message
from flask import current_app
from config import Config
import logging

logger = logging.getLogger(__name__)

mail = Mail()

class EmailService:
    @staticmethod
    def send_temporary_password_email(recipient_email: str, recipient_name: str, temporary_password: str) -> bool:
        """
        Envia email com senha temporária para usuário/cliente.
        
        Args:
            recipient_email: Email do destinatário
            recipient_name: Nome do destinatário
            temporary_password: Senha temporária gerada
            
        Returns:
            bool: True se enviado com sucesso, False caso contrário
        """
        try:
           
            template_email_path = Config.TEMPLATE_EMAIL_PATH
            template_password_path = Config.TEMPLATE_PASSWORD_PATH
            
            # Resolve o caminho absoluto (equivalente ao Server.MapPath)
            path_for_saving = os.path.join(current_app.root_path, template_email_path)
            path_for_saving2 = os.path.join(current_app.root_path, template_password_path)
            
            # Equivalente ao File.ReadAllText
            text = open(path_for_saving, 'r', encoding='utf-8').read()
            contra_text = open(path_for_saving2, 'r', encoding='utf-8').read()
            
            # Equivalente aos Replace do C#
            contra_text = contra_text.replace("*SENHA*", str(temporary_password))
            
            msg_reenvio =  f"""
                Sua solicitação de reenvio de senha temporária foi feita com sucesso!\n; 
                Favor acessar o sitema, digite o seu CPF e a nova temporária, que é:
            """
       
            text = text.replace("*NOME*", recipient_name)
            text = text.replace("*MSG1*", msg_reenvio)
            text = text.replace("*MSG2*", contra_text)
            
            # Envia o email
            msg = Message(
                "Senha Temporária - MonitoraNet",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )
            msg.html = text

            images_dir = os.path.join(current_app.root_path, 'templates', 'imagens')
            
            # Lista de imagens para anexar (arquivo, CID)
            images_list = [
                ('Email_03.jpg', 'Email_03'),
                ('Email_05.jpg', 'Email_05'),
                ('Email_06.jpg', 'Email_06'),
                ('Email_09.jpg', 'Email_09'),
                ('Email_10.jpg', 'Email_10'),
                ('Email_11.jpg', 'Email_11'),
                ('Email_12.jpg', 'Email_12'),
                ('Email_13.jpg', 'Email_13'),
            ]
            
            # Anexa cada imagem com seu CID
            for image_filename, cid_name in images_list:
                image_path = os.path.join(images_dir, image_filename)
                
                if os.path.exists(image_path):
                    with open(image_path, 'rb') as img_file:
                        msg.attach(
                            filename=image_filename,
                            content_type='image/jpeg',
                            data=img_file.read(),
                            disposition='inline',
                            headers=[('Content-ID', f'<{cid_name}>')]
                        )
                    logger.info(f"Image {image_filename} attached with CID: {cid_name}")
                else:
                    logger.warning(f"Image not found: {image_path}")
            
            mail.send(msg)
            logger.info(f"Temporary password email sent to {recipient_email}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {str(e)}")
            return False
            
        except Exception as e:
            logger.error(f"Error sending temporary password email: {str(e)}")
            return False
    @staticmethod
    def send_password_recovery_email(recipient_email: str, recovery_token: str) -> bool:
        """
        DEPRECATED: Use send_temporary_password_email instead.
        Mantido apenas para compatibilidade com código antigo.
        """
        try:
            # Create recovery link
            recovery_url = f"{current_app.config.get('APP_URL_RECOVERY', '')}/{recovery_token}"

            msg = Message(
                "Recuperação de Senha - DocSmart",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )

            msg.html = f"""
            <h2>Recuperação de Senha</h2>
            <p>Você solicitou a recuperação de senha da sua conta.</p>
            <p>Para redefinir sua senha, clique no link abaixo:</p>
            <p><a href="{recovery_url}">Redefinir Senha</a></p>
            <p>Se você não solicitou esta recuperação, ignore este email.</p>
            <p>Este link expira em 1 hora e pode ser usado apenas uma vez.</p>
            """

            mail.send(msg)
            logger.info(f"Recovery email sent to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending recovery email: {str(e)}")
            return False

    @staticmethod
    def send_document_signature_request(recipient_email: str, document_token: str, document_name: str, sender_name: str) -> bool:
        try:
            # Create signature link
            signature_url = f"{current_app.config.get('APP_URL_DOCUMENT_SIGNATURE', '')}/{document_token}"

            msg = Message(
                f"Solicitação de Assinatura de Documento - {document_name}",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )

            msg.html = f"""
            <h2>Solicitação de Assinatura de Documento</h2>
            <p>Você recebeu uma solicitação de assinatura do documento <strong>{document_name}</strong> enviada por <strong>{sender_name}</strong>.</p>
            <p>Para visualizar e assinar o documento, clique no link abaixo:</p>
            <p><a href="{signature_url}">Visualizar e Assinar Documento</a></p>
            <p>Este link expira em 7 dias e pode ser usado apenas uma vez para fins de segurança.</p>
            <p>Se você não estava esperando esta solicitação, por favor ignore este email ou entre em contato com o remetente.</p>
            """

            mail.send(msg)
            logger.info(f"Document signature request email sent to {recipient_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending document signature request email: {str(e)}")
            return False

    @staticmethod
    def send_signed_document_email(recipient_email: str | list, sender_email: str | list, document_name: str, 
                                  sender_name: str, document_path: str, company_name: str = None) -> bool:
        try:
            # Tratar o parâmetro recipient_email para garantir que seja uma lista
            recipients = []
            if recipient_email:
                if isinstance(recipient_email, list):
                    recipients = recipient_email
                else:
                    recipients = [recipient_email]

            # Tratar o parâmetro sender_email para garantir que seja uma lista
            cc_emails = []
            if sender_email:
                if isinstance(sender_email, list):
                    cc_emails = sender_email
                else:
                    cc_emails = [sender_email]

            # Criar mensagem
            msg = Message(
                f"Documento Assinado - {document_name}",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=recipients,
                cc=cc_emails if cc_emails else None
            )

            # Construir corpo do email
            company_info = f" da empresa {company_name}" if company_name else ""

            msg.html = f"""
            <h2>Documento Assinado</h2>
            <p>O documento <strong>{document_name}</strong> foi assinado com sucesso{company_info}.</p>
            <p>O documento assinado está disponível em anexo a este email.</p>
            <p>Este é um processo oficial de assinatura digital realizado através da plataforma DocSmart.</p>
            <p>Se você tiver alguma dúvida, entre em contato com <strong>{sender_name}</strong>.</p>
            """

            # Anexar o documento
            with open(document_path, 'rb') as attachment:
                msg.attach(document_name + ".pdf", "application/pdf", attachment.read())

            # Enviar email
            mail.send(msg)
            logger.info(f"Signed document email sent to {recipients} with CC to {cc_emails}")
            return True

        except Exception as e:
            logger.error(f"Error sending signed document email: {str(e)}")
            return False