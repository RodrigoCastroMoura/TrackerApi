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
    def send_welcome_email(recipient_email: str, recipient_name: str, temporary_password: str) -> bool:
        """
        Envia email de boas-vindas com a senha temporária para um novo usuário/cliente.

        Args:
            recipient_email: Email do destinatário
            recipient_name: Nome do destinatário
            temporary_password: Senha temporária gerada

        Returns:
            bool: True se enviado com sucesso, False caso contrário
        """
        try:
            template_email_path = Config.TEMPLATE_EMAIL_PATH
            path_for_saving = os.path.join(current_app.root_path, template_email_path)

            text = open(path_for_saving, 'r', encoding='utf-8').read()

            msg_boas_vindas = """
                Seja bem-vindo(a) à MonitoraNet! Sua conta foi criada com sucesso.\n
                Esta senha é para o acesso ao aplicativo MonitoraNet. Caso ainda não tenha o aplicativo instalado,
                baixe-o na loja de aplicativos do seu celular (Google Play ou App Store) antes de realizar o primeiro acesso.\n
                Para acessar o aplicativo, utilize seu CPF e a senha temporária abaixo:
            """

            senha_destacada = f"""
                <span style="display: inline-block; margin-top: 8px; padding: 10px 18px; background-color: #0c2033; color: #ffffff; font-size: 20px; font-weight: bold; letter-spacing: 1px; border-radius: 4px;">
                    {temporary_password}
                </span>
            """

            text = text.replace("*NOME*", recipient_name)
            text = text.replace("*MSG1*", msg_boas_vindas)
            text = text.replace("*MSG2*", senha_destacada)

            msg = Message(
                "Bem-vindo(a) - MonitoraNet",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )
            msg.html = text

            images_dir = os.path.join(current_app.root_path, 'templates', 'imagens')

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
            logger.info(f"Welcome email sent to {recipient_email}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"Error sending welcome email: {str(e)}")
            return False

    @staticmethod
    def send_welcome_signature_email(recipient_email: str, recipient_name: str, document_token: str) -> bool:
        """
        Envia email de boas-vindas com o link para assinatura digital do contrato.

        Args:
            recipient_email: Email do destinatário
            recipient_name: Nome do destinatário
            document_token: Token do link de assinatura do documento

        Returns:
            bool: True se enviado com sucesso, False caso contrário
        """
        try:
            template_email_path = Config.TEMPLATE_EMAIL_PATH
            path_for_saving = os.path.join(current_app.root_path, template_email_path)

            text = open(path_for_saving, 'r', encoding='utf-8').read()

            signature_url = f"{current_app.config.get('APP_URL', '').rstrip('/')}/assinar-documento/{document_token}"

            msg_boas_vindas = """
                Seja bem-vindo(a) à MonitoraNet! Sua conta foi criada com sucesso.\n
                Para concluir seu cadastro, falta apenas assinar digitalmente o contrato de prestação de serviços.\n
                Clique no botão abaixo para visualizar e assinar o contrato:
            """

            botao_assinatura = f"""
                <a href="{signature_url}" style="display: inline-block; margin-top: 8px; padding: 10px 18px; background-color: #0c2033; color: #ffffff; font-size: 16px; font-weight: bold; text-decoration: none; border-radius: 4px;">
                    Assinar Contrato
                </a>
            """

            text = text.replace("*NOME*", recipient_name)
            text = text.replace("*MSG1*", msg_boas_vindas)
            text = text.replace("*MSG2*", botao_assinatura)

            msg = Message(
                "Bem-vindo(a) - MonitoraNet",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )
            msg.html = text

            images_dir = os.path.join(current_app.root_path, 'templates', 'imagens')

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
            logger.info(f"Welcome signature email sent to {recipient_email}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"Error sending welcome signature email: {str(e)}")
            return False

    @staticmethod
    def send_signed_welcome_email(recipient_email: str, recipient_name: str, temporary_password: str, document_path: str) -> bool:
        """
        Envia email após a assinatura do contrato: nova senha de acesso ao app,
        orientação para baixar o app nas lojas e o contrato assinado em anexo.

        Args:
            recipient_email: Email do destinatário
            recipient_name: Nome do destinatário
            temporary_password: Nova senha temporária gerada
            document_path: Caminho local do PDF assinado a ser anexado

        Returns:
            bool: True se enviado com sucesso, False caso contrário
        """
        try:
            template_email_path = Config.TEMPLATE_EMAIL_PATH
            path_for_saving = os.path.join(current_app.root_path, template_email_path)

            text = open(path_for_saving, 'r', encoding='utf-8').read()

            msg_boas_vindas = """
                Seu contrato foi assinado com sucesso! Sua conta está pronta para uso.\n
                Para acessar o aplicativo MonitoraNet, baixe-o na loja de aplicativos do seu celular
                (Google Play ou App Store) antes de realizar o primeiro acesso.\n
                Para acessar o aplicativo, utilize seu CPF e a senha temporária abaixo:
            """

            senha_destacada = f"""
                <span style="display: inline-block; margin-top: 8px; padding: 10px 18px; background-color: #0c2033; color: #ffffff; font-size: 20px; font-weight: bold; letter-spacing: 1px; border-radius: 4px;">
                    {temporary_password}
                </span>
            """

            text = text.replace("*NOME*", recipient_name)
            text = text.replace("*MSG1*", msg_boas_vindas)
            text = text.replace("*MSG2*", senha_destacada)

            msg = Message(
                "Contrato Assinado - Bem-vindo(a) - MonitoraNet",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )
            msg.html = text

            images_dir = os.path.join(current_app.root_path, 'templates', 'imagens')

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

            # Anexar o contrato assinado
            with open(document_path, 'rb') as attachment:
                msg.attach("contrato_assinado.pdf", "application/pdf", attachment.read())

            mail.send(msg)
            logger.info(f"Signed welcome email sent to {recipient_email}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"Error sending signed welcome email: {str(e)}")
            return False

    @staticmethod
    def send_welcome_portal_email(recipient_email: str, recipient_name: str, temporary_password: str) -> bool:
        """
        Envia email de boas-vindas com a senha temporária para um novo usuário do portal de configuração.

        Args:
            recipient_email: Email do destinatário
            recipient_name: Nome do destinatário
            temporary_password: Senha temporária gerada

        Returns:
            bool: True se enviado com sucesso, False caso contrário
        """
        try:
            template_email_path = Config.TEMPLATE_EMAIL_PATH
            path_for_saving = os.path.join(current_app.root_path, template_email_path)

            text = open(path_for_saving, 'r', encoding='utf-8').read()

            login_url = current_app.config.get('APP_URL', '')

            msg_boas_vindas = f"""
                Seja bem-vindo(a) à MonitoraNet! Sua conta foi criada com sucesso.\n
                Esta senha é para o acesso ao Portal MonitoraNet, através do navegador em
                <a href="{login_url}">{login_url}</a>.\n
                Para acessar o portal, utilize seu CPF e a senha temporária abaixo:
            """

            senha_destacada = f"""
                <span style="display: inline-block; margin-top: 8px; padding: 10px 18px; background-color: #0c2033; color: #ffffff; font-size: 20px; font-weight: bold; letter-spacing: 1px; border-radius: 4px;">
                    {temporary_password}
                </span>
            """

            text = text.replace("*NOME*", recipient_name)
            text = text.replace("*MSG1*", msg_boas_vindas)
            text = text.replace("*MSG2*", senha_destacada)

            msg = Message(
                "Bem-vindo(a) ao Portal - MonitoraNet",
                sender=current_app.config['MAIL_DEFAULT_SENDER'],
                recipients=[recipient_email]
            )
            msg.html = text

            images_dir = os.path.join(current_app.root_path, 'templates', 'imagens')

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
            logger.info(f"Welcome portal email sent to {recipient_email}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Template file not found: {str(e)}")
            return False

        except Exception as e:
            logger.error(f"Error sending welcome portal email: {str(e)}")
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