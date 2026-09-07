"""Guía de usuario del piloto. Requiere reportlab; no importa la aplicación."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/pdf/Cotizador_Piloto_Resumen_y_Guia.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('UI', 'C:/Windows/Fonts/segoeui.ttf'))
pdfmetrics.registerFont(TTFont('UIBold', 'C:/Windows/Fonts/segoeuib.ttf'))
pdfmetrics.registerFontFamily('UI', normal='UI', bold='UIBold')
NAVY, TEAL, TEXT, MUTED = map(HexColor, ['#153340','#087E83','#243C47','#526B77'])
LIGHT, CORAL = HexColor('#EDF5F5'), HexColor('#B75A32')
W,H = A4
c = canvas.Canvas(str(OUT), pagesize=A4)
c.setTitle('Cotizador Piloto | Novedades y guía para usuarios')
c.setAuthor('EF Perfumes')

def p(text, y, size=11, color=TEXT, width=483, x=56, bold=False):
    style = ParagraphStyle('body', fontName='UIBold' if bold else 'UI', fontSize=size,
        leading=size*1.43, textColor=color)
    paragraph = Paragraph(text, style)
    _,height = paragraph.wrap(width, H)
    paragraph.drawOn(c,x,y-height)
    return y-height

def page(n, title, subtitle):
    c.setFillColor(NAVY); c.rect(0,H-140,W,140,fill=1,stroke=0)
    p('EF PERFUMES  /  COTIZADOR PILOTO',H-28,9,HexColor('#B6DEDE'),bold=True)
    p(title,H-52,25,white,bold=True)
    p(subtitle,H-92,11,white)
    c.setStrokeColor(HexColor('#D4E1E4')); c.line(56,45,W-56,45)
    p('Prueba controlada · 2.0.38-piloto.1 · 07 septiembre 2026',32,8,MUTED)
    p(f'{n} / 3',32,8,MUTED,x=W-82,width=40)
    return H-165

def block(label, body, y):
    y=p(label,y,13,TEAL,bold=True)
    y=p(body,y-6,11)
    return y-22

def callout(title, body, top, height=90):
    c.setFillColor(LIGHT); c.roundRect(48,top-height,W-96,height,9,fill=1,stroke=0)
    p(title,top-13,12,TEAL,x=63,width=W-126,bold=True)
    p(body,top-36,10.5,TEXT,x=63,width=W-126)
    return top-height-22

y=page(1,'Tus cotizaciones, compartidas','Qué cambia y qué podrás probar en esta edición.')
y=callout('El mismo usuario, varios equipos',
    'Las instalaciones habilitadas con tu mismo usuario pueden compartir el histórico de los países y empresas que tengas autorizados.',y,87)
y=block('Un histórico común',
    'Si en un equipo tienes cinco cotizaciones, el otro podrá recibir esas cinco. Si allí creas una nueva, ambos podrán consultar las seis.',y)
y=block('Cambios de estado, pago y tipo',
    'Los cambios de estos campos se comparten sin cambiar los productos ni recalcular los precios del documento original.',y)
y=block('Cada cotización conserva su origen',
    'Podrás identificar su país, empresa, moneda y código original, aunque la consultes desde otra instalación. Los datos históricos del cliente se conservan en el documento.',y)
y=block('Abrir sigue creando una copia',
    '“Abrir Cotización” mantiene su función habitual: preparar una copia con un código nuevo del equipo que la crea. La cotización original se conserva.',y)
y=block('PDF disponible cuando lo necesites',
    'Una cotización recibida puede consultarse y generar su PDF en el equipo actual. No necesita traer el archivo del equipo de origen.',y)
assert y > 58, y
c.showPage()

y=page(2,'Trabajar con más claridad','País, empresa y estado de sincronización a la vista.')
y=block('Elige el contexto antes de cotizar',
    'La selección de país y empresa utiliza tus asignaciones autorizadas. El catálogo y la moneda base corresponden al contexto elegido; las cotizaciones históricas conservan sus valores guardados.',y)
y=block('Guarda y revisa el estado',
    'El guardado local y el envío al servidor son pasos distintos. Con conexión saludable y las ventanas activas, el objetivo es ver el cambio en el otro equipo en unos 10 a 15 segundos después de que el servidor lo confirme. Se comprobará durante el piloto.',y)
y=p('Qué significan los avisos',y,14,TEAL,bold=True)-12
rows=[('Pendiente','Hay un cambio local que todavía debe enviarse.'),
      ('Sincronizado','El cambio fue confirmado; podrá recibirlo el otro equipo.'),
      ('Sin conexión','El cambio se conserva para reintentarlo al recuperar conexión.'),
      ('Conflicto','El documento cambió en otro equipo. Revisa ambas versiones y elige cómo continuar.'),
      ('Histórico incompleto','Faltan datos originales para reproducirlo completo. Se muestra para revisión; no se inventan importes.')]
for i,(label,desc) in enumerate(rows):
    height=58 if i in (3,4) else 43
    if i%2==0:
        c.setFillColor(LIGHT);c.rect(48,y-height,W-96,height,fill=1,stroke=0)
    p(label,y-9,10.5,TEAL,x=59,width=115,bold=True)
    p(desc,y-9,10.5,TEXT,x=186,width=350)
    y-=height
y-=23
y=callout('Eliminar también se comparte',
    'La baja afecta a los equipos que comparten ese histórico. Una eliminación antigua solo se propaga cuando se puede identificar con seguridad el documento correspondiente.',y,91)
assert y > 50, y
c.showPage()

y=page(3,'Una prueba separada','Usa el acceso “Cotizador Piloto” para esta evaluación.')
y=callout('Tu instalación habitual se conserva',
    'El piloto utiliza otra carpeta y su propia base de datos. No copia, reemplaza ni borra la base del Cotizador habitual. Sus documentos y registros se guardan por separado.',y,93)
y=block('La actualización la decides tú',
    'Esta edición no busca ni instala actualizaciones de producción. Para cambiarla, el administrador deberá entregarte otro instalador piloto. La release queda en borrador, sin distribución general.',y)
y=block('Antes de empezar',
    'Completa la configuración del piloto con los datos que indique el administrador. El histórico local comienza separado. Para probar el histórico compartido, el administrador debe preparar el servicio y habilitar al usuario participante.',y)
y=p('Recorrido sugerido de prueba',y,14,TEAL,bold=True)-12
steps=[
    'Abre el piloto en dos equipos con el mismo usuario autorizado.',
    'Crea cinco cotizaciones de prueba en A y comprueba que B las reciba.',
    'Crea la sexta en B y revisa que aparezca en A con el mismo código.',
    'Cambia estado, pago y tipo; abre una copia y genera un PDF.',
    'Prueba una desconexión, una reconexión y la baja de un documento de prueba.'
]
for i,step in enumerate(steps,1):
    p(str(i),y,11,TEAL,x=59,width=20,bold=True)
    y=p(step,y,10.7,TEXT,x=83,width=452)-10
y-=4
y=p('Comparte tus observaciones',y,12,TEAL,bold=True)-5
y=p('Anota qué hiciste, el resultado esperado y el aviso que apareció. Si un documento queda pendiente, incompleto o en conflicto, informa al administrador antes de repetir cambios sobre él.',y,10.5)
assert y > 60, y
c.save()
print(OUT)
