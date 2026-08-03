import os
import re
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER

CONTRACTS_FOLDER = 'contracts'

CONTRATADA_NOME = "Nome da Empresa Contratada"
CONTRATADA_CNPJ = "00.000.000/0000-00"


def _format_cpf(document):
    digits = re.sub(r'\D', '', document or '')
    if len(digits) == 11:
        return f"{digits[0:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"
    return document or ''


def generate_customer_contract(customer):
    """
    Gera o PDF do contrato de prestação de serviços entre a CONTRATADA
    (dados fixos definidos acima) e o cliente (CONTRATANTE) no momento
    da criação do cliente. Retorna o caminho do arquivo PDF gerado.
    """
    if not os.path.exists(CONTRACTS_FOLDER):
        os.makedirs(CONTRACTS_FOLDER)

    nome_arquivo_saida = os.path.join(CONTRACTS_FOLDER, f"contrato_{customer.id}.pdf")

    contratante_nome = customer.name
    contratante_cpf = _format_cpf(customer.document)
    endereco_partes = [f"{customer.street}, {customer.number}"]
    if customer.complement:
        endereco_partes.append(customer.complement)
    endereco_partes.append(f"{customer.district}, {customer.city}/{customer.state}")
    contratante_endereco = " - ".join(endereco_partes)

    contratada_nome = CONTRATADA_NOME
    contratada_cnpj = CONTRATADA_CNPJ

    objeto_contrato = ("prestação de serviços de rastreamento veicular e monitoramento de frota, "
                       "incluindo acesso à plataforma de gestão e acompanhamento em tempo real "
                       "dos veículos vinculados ao CONTRATANTE")
    valor_contrato = "o valor correspondente ao plano de assinatura contratado junto à CONTRATADA"
    prazo_contrato = "prazo indeterminado, vigorando enquanto se mantiver ativa a assinatura contratada"
    cidade_foro = f"{customer.city}/{customer.state}"

    doc = SimpleDocTemplate(
        nome_arquivo_saida,
        pagesize=A4,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle(
        "TituloContrato", parent=styles["Heading1"], alignment=TA_CENTER, spaceAfter=20
    )
    corpo_style = ParagraphStyle(
        "CorpoContrato", parent=styles["Normal"], alignment=TA_JUSTIFY,
        fontSize=11, leading=16, spaceAfter=12
    )
    clausula_titulo_style = ParagraphStyle(
        "ClausulaTitulo", parent=styles["Heading3"], spaceBefore=10, spaceAfter=6
    )

    story = []

    story.append(Paragraph("CONTRATO DE PRESTAÇÃO DE SERVIÇOS", titulo_style))

    qualificacao = f"""
    Pelo presente instrumento particular, de um lado <b>{contratante_nome}</b>,
    portador(a) do CPF nº <b>{contratante_cpf}</b>, residente e domiciliado(a) em
    {contratante_endereco}, doravante denominado(a) <b>CONTRATANTE</b>;
    e de outro lado <b>{contratada_nome}</b>, inscrita no CNPJ sob o nº
    <b>{contratada_cnpj}</b>, doravante denominada <b>CONTRATADA</b>; têm entre si
    justo e contratado o presente instrumento, mediante as cláusulas e condições
    a seguir.
    """
    story.append(Paragraph(qualificacao, corpo_style))

    story.append(Paragraph("CLÁUSULA 1ª - DO OBJETO", clausula_titulo_style))
    story.append(Paragraph(
        f"O presente contrato tem como objeto a {objeto_contrato}.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 2ª - DO VALOR E FORMA DE PAGAMENTO", clausula_titulo_style))
    story.append(Paragraph(
        f"Pelo objeto deste contrato, o(a) CONTRATANTE pagará à CONTRATADA "
        f"{valor_contrato}.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 3ª - DO PRAZO", clausula_titulo_style))
    story.append(Paragraph(
        f"O presente contrato vigorará por {prazo_contrato}, "
        f"a contar da data de sua assinatura.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 4ª - DAS OBRIGAÇÕES DAS PARTES", clausula_titulo_style))
    story.append(Paragraph(
        "As partes se comprometem a cumprir fielmente o que foi acordado neste "
        "instrumento, agindo com boa-fé e observando a legislação aplicável.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 5ª - DA RESCISÃO", clausula_titulo_style))
    story.append(Paragraph(
        "O presente contrato poderá ser rescindido por qualquer das partes, mediante "
        "comunicação prévia por escrito com antecedência mínima de 30 (trinta) dias, "
        "ou imediatamente em caso de descumprimento de quaisquer das cláusulas aqui "
        "estabelecidas.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 6ª - DA CONFIDENCIALIDADE", clausula_titulo_style))
    story.append(Paragraph(
        "As partes se comprometem a manter sigilo sobre todas as informações "
        "confidenciais trocadas em razão deste contrato, não as divulgando a "
        "terceiros sem autorização prévia e por escrito da outra parte, mesmo após "
        "o término da vigência contratual.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 7ª - DAS DISPOSIÇÕES GERAIS", clausula_titulo_style))
    story.append(Paragraph(
        "O presente contrato representa a integralidade do acordo entre as partes, "
        "substituindo quaisquer entendimentos anteriores sobre o mesmo objeto. "
        "Alterações a este contrato somente serão válidas se realizadas por escrito "
        "e assinadas por ambas as partes.",
        corpo_style
    ))

    story.append(Paragraph("CLÁUSULA 8ª - DO FORO", clausula_titulo_style))
    story.append(Paragraph(
        f"Fica eleito o foro da comarca de {cidade_foro} para dirimir quaisquer "
        f"controvérsias oriundas do presente contrato.",
        corpo_style
    ))

    data_extenso = date.today().strftime("%d/%m/%Y")
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        f"{cidade_foro.split('/')[0]}, {data_extenso}.",
        ParagraphStyle("Data", parent=corpo_style, alignment=TA_CENTER)
    ))

    story.append(Spacer(1, 50))
    assinatura_style = ParagraphStyle("Assinatura", parent=styles["Normal"], alignment=TA_CENTER)
    story.append(Paragraph("_______________________________________", assinatura_style))
    story.append(Paragraph(f"{contratante_nome}<br/>CPF: {contratante_cpf}<br/>CONTRATANTE", assinatura_style))
    story.append(Spacer(1, 30))
    story.append(Paragraph("_______________________________________", assinatura_style))
    story.append(Paragraph(f"{contratada_nome}<br/>CNPJ: {contratada_cnpj}<br/>CONTRATADA", assinatura_style))

    doc.build(story)

    return nome_arquivo_saida
