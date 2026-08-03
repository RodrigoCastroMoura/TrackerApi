from flask import request
from flask_restx import Namespace, Resource, fields
from app.presentation.auth_routes import customer_token_required, generate_temporary_password
from app.domain.models import Document, Customer
from app.infrastructure.firebase_storage import FirebaseStorage
from app.infrastructure.email_service import EmailService
import os
import fitz  # PyMuPDF
import time
import traceback
import gc
import werkzeug.utils
import logging
from bson.objectid import ObjectId
from mongoengine.errors import DoesNotExist
import base64
from datetime import datetime

logger = logging.getLogger(__name__)

api = Namespace('pdf-analyzer', description='PDF Pattern Analysis operations')

# Define models for Swagger documentation
signature_area_model = api.model(
    'SignatureArea', {
        'rect':
        fields.Nested(
            api.model(
                'Rectangle', {
                    'x0':
                    fields.Float(
                        description='X-coordinate of top-left corner'),
                    'y0':
                    fields.Float(
                        description='Y-coordinate of top-left corner'),
                    'x1':
                    fields.Float(
                        description='X-coordinate of bottom-right corner'),
                    'y1':
                    fields.Float(
                        description='Y-coordinate of bottom-right corner')
                })),
        'type':
        fields.String(description='Type of area (signature line)'),
        'text':
        fields.String(description='Text of the signature line'),
        'text_below':
        fields.String(description='Text below the signature line'),
        'has_description':
        fields.Boolean(
            description='Whether the signature line has a description')
    })

page_position_model = api.model(
    'PagePosition', {
        'posicao_x': fields.Float(description='X position for footer'),
        'posicao_y': fields.Float(description='Y position for footer'),
        'largura_pagina': fields.Float(description='Page width'),
        'altura_pagina': fields.Float(description='Page height')
    })

pdf_analysis_model = api.model(
    'PdfAnalysisResults',
    {'resultados': fields.Raw(description='Analysis results by page number')})

pdf_document_analysis_model = api.model(
    'PdfDocumentAnalysisResults', {
        'document_id': fields.String(description='Document ID'),
        'document_name': fields.String(description='Document name'),
        'resultados': fields.Raw(description='Analysis results by page number')
    })

dependency_check_model = api.model(
    'DependencyCheck', {
        'PyPDF2': fields.Boolean(description='PyPDF2 library availability'),
        'PyMuPDF': fields.Boolean(description='PyMuPDF library availability'),
        'OpenCV': fields.Boolean(description='OpenCV library availability'),
        'Tesseract': fields.Boolean(description='Tesseract OCR availability'),
        'PIL': fields.Boolean(description='Pillow library availability'),
        'NumPy': fields.Boolean(description='NumPy library availability')
    })

error_model = api.model('Error',
                        {'erro': fields.String(description='Error message')})

# Create temporary upload folder
UPLOAD_FOLDER = 'temp_uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Define models for Swagger documentation
upload_parser = api.parser()

upload_parser.add_argument('pdf',
                           location='files',
                           type='file',
                           required=True,
                           help='PDF file to analyze')


upload_parser.add_argument('find',
                           location='args',
                           type=str,
                           default='',
                           help='Index of the signature occurrence on the page')


def buscar_padroes_no_pdf(caminho_pdf, find = "", cleanup_file=True):
    """
    Função principal para buscar padrões em um PDF, com tratamento de erros robusto.
    VERSÃO CORRIGIDA para resolver problemas de file handles no Windows.
    """
    resultados = {}
    doc_fitz = None
    pages = []  # Lista para armazenar referências das páginas

    try:
        logger.info(f"Iniciando análise do PDF: {caminho_pdf}")

        # Abrir PDF de forma segura
        doc_fitz = safe_open_pdf_local(caminho_pdf)
        num_paginas = len(doc_fitz)

        logger.debug(f"PDF aberto com {num_paginas} páginas")

        for num_pagina in range(num_paginas):
            try:
                page = doc_fitz[num_pagina]
                pages.append(page)  # Manter referência para cleanup posterior

                status = find_signature_lines(page, find)

                if len(status) > 0:
                    resultados[num_pagina] = status
                else:
                    resultados[num_pagina] = find_boton_lines(page)

                # NÃO deletar a página aqui - faremos no finally

            except Exception as e:
                logger.error(f"Erro ao processar página {num_pagina}: {e}")
                resultados[num_pagina] = {"error": f"Erro na página {num_pagina}: {str(e)}"}

        logger.info(f"Análise concluída. {len(resultados)} páginas processadas")
        return resultados

    except Exception as e:
        erro_completo = traceback.format_exc()
        logger.error(f"Erro geral ao processar o arquivo: {e}\n{erro_completo}")
        return {}, {"error": str(e), "traceback": erro_completo}

    finally:
        # CLEANUP CRÍTICO: Limpar tudo na ordem correta
        try:
            # 1. Limpar referências das páginas primeiro
            for page in pages:
                try:
                    del page
                except:
                    pass
            pages.clear()

            # 2. Forçar coleta de lixo após limpar páginas
            gc.collect()
            time.sleep(0.1)

            # 3. Fechar documento de forma segura
            if doc_fitz:
                safe_close_pdf_local(doc_fitz)
                doc_fitz = None

            # 4. Aguardar liberação completa antes do cleanup
            time.sleep(0.3)
            gc.collect()

            # 5. Cleanup do arquivo se solicitado
            if cleanup_file and caminho_pdf:
                logger.info(f"Iniciando cleanup do arquivo: {caminho_pdf}")

                # Usar função existente ou fallback
                try:
                    success = cleanup_temp_file_aggressive(caminho_pdf)
                except NameError:
                    success = cleanup_temp_file_local(caminho_pdf)

                if success:
                    logger.info(f"Arquivo removido com sucesso: {caminho_pdf}")
                else:
                    logger.warning(f"Falha ao remover arquivo: {caminho_pdf}")

        except Exception as cleanup_error:
            logger.error(f"Erro durante cleanup: {cleanup_error}")


def cleanup_temp_file_local(file_path, max_retries=15):
    """
    Função local de cleanup para casos onde cleanup_temp_file_aggressive não está disponível
    """
    if not file_path or not os.path.exists(file_path):
        return True

    logger.info(f"Iniciando limpeza local: {file_path}")

    # Forçar múltiplas coletas de lixo
    for i in range(7):
        gc.collect()
        time.sleep(0.3)

    # Tentar métodos diferentes de remoção
    removal_methods = [
        lambda path: os.remove(path),
        lambda path: os.unlink(path),
    ]

    for retry in range(max_retries):
        for method_idx, remove_method in enumerate(removal_methods):
            try:
                # Verificar se ainda existe
                if not os.path.exists(file_path):
                    logger.info(f"Arquivo removido com sucesso: {file_path}")
                    return True

                # Tentar remover
                remove_method(file_path)

                # Verificar se foi removido
                if not os.path.exists(file_path):
                    logger.info(f"Arquivo removido com método {method_idx + 1}: {file_path}")
                    return True

            except PermissionError as e:
                if retry < max_retries - 1:
                    wait_time = min(5.0, (retry + 1) * 0.7)
                    logger.warning(
                        f"Tentativa {retry + 1}/{max_retries} (método {method_idx + 1}) falhou: {e}. "
                        f"Aguardando {wait_time}s..."
                    )
                    time.sleep(wait_time)

                    # Limpeza adicional entre tentativas
                    for _ in range(5):
                        gc.collect()
                        time.sleep(0.2)
                else:
                    logger.error(f"FALHA TOTAL após {max_retries} tentativas: {file_path} - {e}")
                    return False

            except Exception as e:
                logger.error(f"Erro inesperado método {method_idx + 1}: {e}")
                break

    logger.error(f"Falha ao remover arquivo após todas as tentativas: {file_path}")
    return False


def safe_open_pdf_local(file_path, retries=3):
        """Função local para abrir PDF com retry"""
        for attempt in range(retries):
            try:
                doc = fitz.open(file_path)
                if hasattr(globals(), 'register_document'):
                    register_document(doc)
                return doc
            except Exception as e:
                if attempt < retries - 1:
                    logger.warning(f"Tentativa {attempt + 1} falhou ao abrir {file_path}: {e}")
                    time.sleep(0.5)
                    gc.collect()
                else:
                    raise e


def safe_close_pdf_local(doc):
        """Função local para fechar PDF de forma segura"""
        if not doc:
            return
        try:
            # Limpar todas as páginas primeiro
            if hasattr(doc, 'page_count'):
                for i in range(doc.page_count):
                    try:
                        page = doc[i]
                        del page
                    except:
                        pass

            # Fechar o documento
            if not doc.is_closed:
                doc.close()

            # Deletar referência
            del doc

            # Forçar coleta de lixo múltiplas vezes
            for _ in range(3):
                gc.collect()
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Erro ao fechar PDF: {e}")


def save_signature_no_pdf(caminho_pdf, signature, rubric, find = ''):
    """
    Função principal para salvar a assinatura no PDF, com tratamento de erros robusto.
    """
    doc_fitz = None
    try:
        output_path = caminho_pdf.replace('.pdf', '_assinado.pdf')
        doc_fitz = fitz.open(caminho_pdf)
        num_paginas = len(doc_fitz)
        for num_pagina in range(num_paginas):
            page = doc_fitz[num_pagina]

            rect = find_signature_lines(page, find)

            if len(rect) > 0:
                draw_signature(page, rect, signature)
            else:
                rect = find_boton_lines(page)
                draw_signature(page, rect, rubric, find)

    except Exception as e:
        erro_completo = traceback.format_exc()
        logger.error(
            f"Erro geral ao processar o arquivo: {e}\n{erro_completo}")
    finally:
        # Always close the document in the finally block
        if doc_fitz:
            try:
                doc_fitz.save(output_path)
                doc_fitz.close()
                return output_path
            except Exception as close_error:
                logger.error(f"Erro ao fechar documento PDF: {close_error}")


def find_signature_lines(page, find=""):
    """
    Procura por possíveis locais de assinatura no PDF baseado em padrões comuns
    """
    signature_areas = []

    # Obter todo o texto da página com informações de posicionamento
    words = page.get_text("words")

    # Organizar palavras por posição vertical (y)
    words_by_y = {}
    for word in words:
        if len(
                word
        ) >= 5:  # Garantir que temos pelo menos as coordenadas e o texto
            x0, y0, x1, y1, text = word[:5]
            if y0 not in words_by_y:
                words_by_y[y0] = []
            words_by_y[y0].append((x0, y0, x1, y1, text))

    # Ordenar as coordenadas y
    y_positions = sorted(words_by_y.keys())

    for i, y_pos in enumerate(y_positions):
        for word in words_by_y[y_pos]:
            x0, y0, x1, y1, text = word

            # Verificar padrões de assinatura
            is_signature_line = (
                # Linha de underscore
                text.strip('_') == '' and len(text) >= 20
                or text.strip(f'._') == '' and len(text) >= 20)

            if is_signature_line:
                # Procurar texto abaixo da linha
                text_below = ""
                if i + 1 < len(y_positions):
                    next_y = y_positions[i + 1]
                    # Verificar se a próxima linha está próxima (dentro de 20 pontos)
                    if next_y - y1 < 20:
                        # Concatenar todo o texto da linha abaixo
                        text_below = " ".join(word[4]
                                              for word in words_by_y[next_y])

                 # Criar área retangular para a linha de assinatura
                rect_obj = fitz.Rect(x0, y0, x1, y1)

                signature_area = {
                    'rect': {
                        'x0': rect_obj.x0,
                        'y0': rect_obj.y0,
                        'x1': rect_obj.x1,
                        'y1': rect_obj.y1,
                        'text': text,
                        'text_below': text_below,
                    },
                    'has_signature': True
                }
                signature_areas.append(signature_area)

    if not signature_areas:
        return {}
    if find == '':
        for signature_area in signature_areas:
            if signature_area['rect']['text'].strip(f'._') == '' and signature_area['rect']['text'].strip(f'_'):
                return signature_area
        index = 0
    else:
        index = int(find) - 1
    return signature_areas[index]


def find_boton_lines(pagina):
    """
    Localiza posições estratégicas para inserção de rubricas no PDF.
    Retorna coordenadas para 2 rubricas: uma no rodapé e outra no meio da página.
    """
    try:
        # Obter dimensões da página
        altura_pagina = pagina.rect.height
        largura_pagina = pagina.rect.width

        # Posição para rubrica no rodapé (última linha)
        rodape_x = largura_pagina * 0.8  # Posicionar a 80% da largura (mais à direita)
        rodape_y = altura_pagina * 0.95  # Posicionar a 95% da altura (bem no final)

        # Posição para rubrica no meio da página
        meio_x = largura_pagina / 2   # Mais à direita no meio da página
        meio_y = altura_pagina * 0.5     # Exatamente no meio vertical

        # Obter texto do rodapé para verificar se existe conteúdo lá
        regiao_rodape = fitz.Rect(0, altura_pagina * 0.75, largura_pagina, altura_pagina)
        texto_rodape = pagina.get_text("text", clip=regiao_rodape)

        # Ajustar a posição vertical caso encontre texto no rodapé
        if texto_rodape.strip():
            # Extrair as linhas do texto do rodapé
            linhas = texto_rodape.strip().split('\n')

            # Se houver múltiplas linhas, ajustar para a última
            if len(linhas) > 1:
                # Procurar a posição Y da última linha de texto
                palavras_rodape = pagina.get_text("words", clip=regiao_rodape)
                if palavras_rodape:
                    # Encontrar a palavra com maior coordenada Y (mais abaixo na página)
                    max_y = max([palavra[3] for palavra in palavras_rodape if len(palavra) >= 4])
                    rodape_y = max_y + 5  # Posicionar 5 pontos abaixo da última linha de texto

        # Verificar se há texto no meio da página para evitar sobrepor conteúdo importante
        regiao_meio = fitz.Rect(largura_pagina * 0.7, altura_pagina * 0.45,
                               largura_pagina, altura_pagina * 0.55)
        texto_meio = pagina.get_text("text", clip=regiao_meio)


        return {
             'rect': {
                'x0': rodape_x,
                'y0': rodape_y,
                'x1': rodape_x + 40,  # Largura aproximada para uma rubrica
                'y1': rodape_y + 20,  # Altura aproximada para uma rubrica
                'xm': meio_x,
                'ym': meio_y
            },
            'has_signature': False
        }

    except Exception as e:
        logger.error(f"Erro ao processar posições para rubrica na página: {str(e)}")
        # Em caso de erro, retornar posições padrão
        return {
             'rect': {
                'x0': largura_pagina * 0.8,
                'y0': altura_pagina * 0.95,
                'x1': largura_pagina * 0.8 + 40,
                'y1': altura_pagina * 0.95 + 20,
                'ym':meio_y
            },
            'has_signature': False
        }


def draw_signature(page, rect, signature, find = ''):
    """
    Insere uma imagem de assinatura no PDF
    """
    try:
        # 1. Remover o prefixo "data:image/png;base64,"
        if signature.startswith("data:image/png;base64,"):
            signature = signature.split(",")[1]  # Pega apenas a parte após a vírgula

        # 2. Decodificar a base64 para obter os dados binários
        image_data = base64.b64decode(signature)

        # Criar um objeto de imagem do PyMuPDF
        img = fitz.Pixmap(image_data)

        if find != '':
            position = 50 * int(f'{find}')
        else:
            position = 0

         # Calcular largura e altura do campo
          # Calcular largura e altura do campo
        if rect['has_signature']:
            field_width = (rect['rect']['x1'] + 6)- rect['rect']['x0']
            field_height = (rect['rect']['y1'] + 8 )- rect['rect']['y0']
        else:
            field_width = (rect['rect']['x1'] + 40)- rect['rect']['x0']
            field_height = (rect['rect']['y1'] + 20 )- rect['rect']['y0']

        # Calcular razão de aspecto
        img_ratio = img.width / img.height

        # Definir tamanhos máximos relativos ao campo
        max_width = field_width * 2.0  # 100% da largura do campo
        max_height = field_height * 1.5  # 90% da altura do campo

        # Calcular dimensões mantendo a proporção
        if img_ratio > 1:
            # Imagem é mais larga que alta
            draw_width = min(max_width, field_width)
            draw_height = draw_width / img_ratio

            # Se altura for muito grande, redimensionar
            if draw_height > max_height:
                draw_height = max_height
                draw_width = draw_height * img_ratio
        else:
            # Imagem é mais alta que larga ou quadrada
            draw_height = min(max_height, field_height)
            draw_width = draw_height * img_ratio

            # Se largura for muito grande, redimensionar
            if draw_width > max_width:
                draw_width = max_width
                draw_height = draw_width / img_ratio

        # Centralizar a imagem no campo
        if rect['has_signature']:
            offset_x = rect['rect']['x0'] + (field_width - draw_width) / 2
            offset_y = (rect['rect']['y0'] - 10) + (field_height - draw_height) / 2 -5
        else:
            ym = rect['rect']['xm']
            offset_x = (ym + position ) + (field_width - draw_width) / 2
            offset_y = (rect['rect']['y0'] + 15) + (field_height - draw_height) / 2 -5



        # Inserir a imagem no PDF
        page.insert_image(fitz.Rect(offset_x, offset_y, offset_x + draw_width, offset_y + draw_height), pixmap=img)

    except Exception as e:
        logger.error(f"Erro ao criar assinatura: {str(e)}")
        raise e


@api.route('')
class PdfAnalyzer(Resource):

    @api.doc('analyze_pdf',
             responses={
                 200: 'PDF analyzed successfully',
                 400: 'Invalid input',
                 500: 'Server error'
             })
    @api.expect(upload_parser)
    @api.response(200, 'Success', pdf_analysis_model)
    @api.response(400, 'Invalid input', error_model)
    @api.response(500, 'Server error', error_model)
    def post(self):
        """
        Analyze PDF to detect specific patterns

        This endpoint analyzes PDF documents looking for signature lines and RG document patterns.
        """
        try:
            # Verify if file was sent
            if 'pdf' not in request.files:
                return {'erro': 'Nenhum arquivo enviado'}, 400

            arquivo = request.files['pdf']

            # Verify if filename is empty
            if arquivo.filename == '':
                return {'erro': 'Nome do arquivo vazio'}, 400

            # Get find parameter
            find = request.args.get('find', '')

            # Save file temporarily
            nome_arquivo = werkzeug.utils.secure_filename(arquivo.filename)
            caminho_temp = os.path.join(UPLOAD_FOLDER, nome_arquivo)
            arquivo.save(caminho_temp)

            # Process PDF
            resultados = buscar_padroes_no_pdf(caminho_temp, find=find)

            # Create response
            resposta = {'resultados': resultados}

            return resposta, 200

        except Exception as e:
            # Ensure temporary file is removed in case of error
            if 'caminho_temp' in locals() and os.path.exists(caminho_temp):
                os.remove(caminho_temp)

            logger.error(f"Error analyzing PDF: {str(e)}", exc_info=True)
            return {'erro': str(e)}, 500

@api.route('/document/<id>')
@api.param('id', 'Document ID', required=True)
class PdfDocumentAnalyzer(Resource):

    @api.doc('analyze_pdf_by_document_id', responses={
                 200: 'PDF analyzed successfully',
                 400: 'Invalid input',
                 403: 'Unauthorized',
                 404: 'Document not found',
                 500: 'Server error'
             })
    @api.response(200, 'Success', pdf_document_analysis_model)
    @api.response(400, 'Invalid input', error_model)
    @api.response(403, 'Unauthorized', error_model)
    @api.response(404, 'Document not found', error_model)
    @api.response(500, 'Server error', error_model)
    @customer_token_required
    def get(self, current_customer, id):
        """
        Analyze PDF by document ID

        This endpoint analyzes PDF documents by document ID, looking for signature lines and RG document patterns.
        """
        try:

            # Verify if the document ID is valid
            if not ObjectId.is_valid(id):
                return {'erro': 'ID do documento inválido'}, 400

            # Get the document
            try:
                document = Document.objects.get(id=id, status='active')
            except DoesNotExist:
                return {'erro': 'Documento não encontrado'}, 404

            # Check if user has access to the document
            if current_customer.role != 'admin' and str(
                    current_customer.id) != str(document.customer_id.id):
                return {'erro': 'Não autorizado a acessar este documento'}, 403

            # Get document URL
            url = document.url
            if not url:
                return {'erro': 'URL do documento não encontrada'}, 404

            # Criar um nome para o arquivo temporário
            nome_arquivo = werkzeug.utils.secure_filename(f"doc_{id}.pdf")
            caminho_temp = os.path.join(UPLOAD_FOLDER, nome_arquivo)

            try:
                # Se a URL for um caminho local
                if os.path.exists(url):
                    import shutil
                    shutil.copy2(url, caminho_temp)
                # Se a URL for uma URL web
                else:
                    import requests
                    response = requests.get(url, stream=True)
                    if response.status_code != 200:
                        return {
                            'erro':
                            f'Erro ao baixar o arquivo: {response.status_code}'
                        }, 404
                    with open(caminho_temp, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                # Process PDF using the existing function
                find = request.args.get('find', '')

                resultados = buscar_padroes_no_pdf(caminho_temp, find)

                # Limpar arquivo temporário após uso
                time.sleep(
                    0.5
                )  # Aguardar para garantir que o arquivo não está em uso
                gc.collect()  # Forçar coleta de lixo

                # Close any open file handles
                if 'doc_fitz' in locals() and doc_fitz:
                    doc_fitz.close()

                # Try to remove file with retries
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        if os.path.exists(caminho_temp):
                            os.remove(caminho_temp)
                        break  # Successfully removed the file
                    except PermissionError as e:
                        if retry < max_retries - 1:
                            logger.warning(
                                f"File {caminho_temp} is still in use, retrying in 1 second...{e}"
                            )
                            time.sleep(1)  # Wait a bit longer
                            gc.collect()
                        else:
                            logger.error(
                                f"Could not remove temporary file {caminho_temp} after {max_retries} attempts"
                            )

            except Exception as e:
                # Garantir que o arquivo temporário seja removido em caso de erro
                # Same retry logic for exception handler
                if os.path.exists(caminho_temp):
                    try:
                        os.remove(caminho_temp)
                    except PermissionError:
                        logger.error(
                            f"Could not remove temporary file {caminho_temp} during exception handling: {str(e)}"
                        )
                raise e

            # Create response
            resposta = {
                'document_id': str(document.id),
                'document_name': os.path.basename(document.url),
                'resultados': resultados
            }

            return resposta, 200

        except Exception as e:
            logger.error(f"Error analyzing PDF by document ID: {str(e)}",
                         exc_info=True)
            return {'erro': str(e)}, 500

    @api.doc('save_pdf_by_document_id',
             responses={
                 200: 'PDF save successfully',
                 400: 'Invalid input',
                 403: 'Unauthorized',
                 404: 'Document not found',
                 500: 'Server error'
             })
    @api.response(200, 'Success', pdf_document_analysis_model)
    @api.response(400, 'Invalid input', error_model)
    @api.response(403, 'Unauthorized', error_model)
    @api.response(404, 'Document not found', error_model)
    @api.response(500, 'Server error', error_model)
    @customer_token_required
    def post(self, current_customer, id):
        """
        Analyze PDF by document ID

        This endpoint analyzes PDF documents by document ID, looking for signature lines and RG document patterns.
        """
        try:
            # Verify if the document ID is valid
            if not ObjectId.is_valid(id):
                return {'erro': 'ID do documento inválido'}, 400

            # Get the document
            try:
                document = Document.objects.get(id=id, status='active')
            except DoesNotExist:
                return {'erro': 'Documento não encontrado'}, 404

            try:
                customer = Customer.objects.get(id=current_customer.id, status='active')
            except DoesNotExist:
                return {'erro': 'Cliente não encontrado'}, 404

            # Check if user has access to the document
            if current_customer.role != 'admin' and str(
                    current_customer.id) != str(document.customer_id.id):
                return {'erro': 'Não autorizado a acessar este documento'}, 403

            # Get document URL
            url = document.url
            if not url:
                return {'erro': 'URL do documento não encontrada'}, 404

            agora = datetime.now()
            data_str = agora.strftime("%Y-%m-%d %H:%M:%S")

            # Criar um nome para o arquivo temporário
            nome_arquivo = werkzeug.utils.secure_filename(f"doc_{id}{data_str}.pdf")
            caminho_temp = os.path.join(UPLOAD_FOLDER, nome_arquivo)

            try:
                # Se a URL for um caminho local
                if os.path.exists(url):
                    import shutil
                    shutil.copy2(url, caminho_temp)
                # Se a URL for uma URL web
                else:
                    import requests
                    response = requests.get(url, stream=True)
                    if response.status_code != 200:
                        return {
                            'erro':
                            f'Erro ao baixar o arquivo: {response.status_code}'
                        }, 404
                    with open(caminho_temp, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                find = ''

                # Process save PDF using the existing function
                name_file_signature = save_signature_no_pdf(caminho_temp, customer.signatureDoc, customer.rubricDoc, find)

                try:
                    # Delete old file from Firebase if it exists
                    if document.url:
                        try:
                            storage = FirebaseStorage()
                            storage.delete_file(document.url)
                        except Exception as e:
                            logger.warning(f"Error deleting old file: {str(e)}")

                    # Upload new file
                    storage = FirebaseStorage()
                    filename = os.path.basename(name_file_signature)
                    with open(name_file_signature, 'rb') as file:
                        file.filename = filename  # Add filename attribute
                        file_url = storage.save_file(file)
                    document.url = file_url
                    document.signature = True
                    document.save()

                    # Gerar nova senha de acesso ao app e enviar por email junto com o contrato assinado
                    temporary_password = generate_temporary_password()
                    customer.set_password(temporary_password)
                    customer.save()

                    email_sent = EmailService.send_signed_welcome_email(
                        customer.email,
                        customer.name,
                        temporary_password,
                        name_file_signature
                    )
                    if not email_sent:
                        logger.error(f"Erro ao enviar email de documento assinado: {customer.email}, document_Id: {document.id}")

                    # Cleanup temporary file
                    if os.path.exists(name_file_signature):
                        os.remove(name_file_signature)
                except Exception as e:
                    logger.error(f"Error handling file: {str(e)}")
                    return {'message': 'Erro ao processar arquivo'}, 500

                # Limpar arquivo temporário após uso
                time.sleep(
                    0.5
                )  # Aguardar para garantir que o arquivo não está em uso
                gc.collect()  # Forçar coleta de lixo

                # Close any open file handles
                if 'doc_fitz' in locals() and doc_fitz:
                    doc_fitz.close()

                # Try to remove file with retries
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        if os.path.exists(caminho_temp):
                            os.remove(caminho_temp)
                            nome_arquivo_ = werkzeug.utils.secure_filename(f"doc_{id}.pdf")
                            caminho_temp_ = os.path.join(UPLOAD_FOLDER, nome_arquivo_)
                            if os.path.exists(caminho_temp_):
                                os.remove(caminho_temp_)

                        break  # Successfully removed the file
                    except PermissionError:
                        if retry < max_retries - 1:
                            logger.warning(
                                f"File {caminho_temp} is still in use, retrying in 1 second..."
                            )
                            time.sleep(1)  # Wait a bit longer
                            gc.collect()
                        else:
                            logger.error(
                                f"Could not remove temporary file {caminho_temp} after {max_retries} attempts"
                            )

            except Exception as e:
                # Garantir que o arquivo temporário seja removido em caso de erro
                # Same retry logic for exception handler
                if os.path.exists(caminho_temp):
                    try:
                        os.remove(caminho_temp)
                    except PermissionError:
                        logger.error(
                            f"Could not remove temporary file {caminho_temp} during exception handling: {str(e)}"
                        )
                raise e

            # Create response
            resposta = {
                'document_id': str(document.id),
                'document_name': os.path.basename(document.url),
                'resultados': []
            }

            return resposta, 200

        except Exception as e:
            logger.error(f"Error analyzing PDF by document ID: {str(e)}",
                         exc_info=True)
            return {'erro': str(e)}, 500
