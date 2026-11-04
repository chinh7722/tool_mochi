"""Local Qt charts over the VPS ledger; no local accounting database."""
from datetime import datetime
from decimal import Decimal
from PySide6.QtCore import Qt, QRectF, Signal, QDate
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QDateEdit,QComboBox,QTabWidget,QScrollArea,QGridLayout)
from core.job_schedule import VIETNAM_TIME

COLORS = ['#60a5fa','#34d399','#fbbf24','#fb7185','#a78bfa','#22d3ee','#f97316']


def money(value):
    return f'{Decimal(value)/100:,.2f}'


class DailyChart(QWidget):
    clicked = Signal(str)

    def __init__(self, field, title, parent=None):
        super().__init__(parent)
        self.field,self.title,self.rows,self.regions = field,title,[],[]
        self.setMinimumHeight(235)
        self.setMinimumWidth(360)
        self.setMouseTracking(True)

    def set_rows(self, rows):
        self.rows = rows
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor('#e2e8f0'))
        p.drawText(12,22,self.title)
        values = [r[self.field] for r in self.rows]
        hi,lo = max([1]+values),min([0]+values)
        left,top,width,height = 58,48,max(1,self.width()-72),self.height()-84
        zero = top+height*hi/(hi-lo)
        p.setPen(QColor('#64748b'))
        p.drawLine(int(left),int(zero),int(left+width),int(zero))
        label = money(hi) if self.field=='profit_units' else str(hi)
        p.drawText(2,45,label)
        p.drawText(2,int(top+height),money(lo) if self.field=='profit_units' else str(lo))
        self.regions=[]
        for index,r in enumerate(self.rows):
            step=width/max(1,len(self.rows))
            x=left+index*step
            y=zero-height*r[self.field]/(hi-lo)
            rect=QRectF(x+1,min(y,zero),max(2,step-3),max(2,abs(zero-y)))
            p.fillRect(rect,QColor('#fb7185' if r[self.field]<0 else '#60a5fa'))
            self.regions.append((QRectF(x,top,step,height+24),r))
            p.setPen(QColor('#94a3b8'))
            if index%2==0:
                p.drawText(QRectF(x,top+height+8,step,18),Qt.AlignmentFlag.AlignCenter,r['day'][-2:])
        p.end()

    def mouseMoveEvent(self,event):
        for rect,row in self.regions:
            if rect.contains(event.position()):
                value=money(row[self.field])+' VND' if self.field=='profit_units' else str(row[self.field])
                self.setToolTip(row['day']+': '+value+(' · Còn dữ liệu chờ đối soát' if row.get('pending_jobs') else ''))
                return

    def mousePressEvent(self,event):
        for rect,row in self.regions:
            if rect.contains(event.position()):
                self.clicked.emit(row['day'])
                return


class ReasonChart(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.rows=[]
        self.setMinimumHeight(260)

    def set_rows(self,rows):
        self.rows=rows
        self.update()

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor('#e2e8f0'));p.drawText(12,22,'Tỷ lệ lý do thất bại của các đợt chạy')
        total=sum(r['count'] for r in self.rows)
        if not total:
            p.drawText(12,65,'Chưa có đợt chạy thất bại trong tháng.');return
        start=0
        size=min(180,self.height()-55)
        for i,row in enumerate(self.rows):
            span=round(row['count']/total*5760)
            p.setBrush(QColor(COLORS[i%len(COLORS)]));p.setPen(Qt.PenStyle.NoPen)
            p.drawPie(QRectF(12,42,size,size),start,span);start+=span
            p.setPen(QColor(COLORS[i%len(COLORS)]))
            text=f"{row['count']/total:.1%} ({row['count']}) · {row['reason']}"
            p.drawText(QRectF(size+28,43+i*46,self.width()-size-40,44),Qt.TextFlag.TextWordWrap,text)
        p.end()


class StatisticsPanel(QWidget):
    def __init__(self,window):
        super().__init__()
        from gui.remote_window import table
        self.window=window
        self.busy=False;self.pending=False;self.day=None;self.offset=0;self.mode='jobs'
        box=QVBoxLayout(self)
        box.setContentsMargins(24,20,24,16)
        title=QLabel('Thống kê');title.setStyleSheet('font-size:24px;font-weight:700;');box.addWidget(title)
        row=QHBoxLayout()
        self.month=QDateEdit();self.month.setDisplayFormat('MM/yyyy');self.month.setCalendarPopup(True)
        today=datetime.now(VIETNAM_TIME).date();self.month.setDate(QDate(today.year,today.month,1))
        self.owner=QComboBox();self.owner.addItem('Tất cả mọi người',None)
        row.addWidget(QLabel('Tháng:'));row.addWidget(self.month);row.addWidget(self.owner,1)
        for label,callback in (('Cả tháng',self.all_days),('Làm mới',self.load)):
            button=QPushButton(label);button.clicked.connect(callback);row.addWidget(button)
        box.addLayout(row)
        self.note=QLabel('Chọn tháng và bấm vào cột để xem chi tiết.');self.note.setWordWrap(True);box.addWidget(self.note)
        self.tabs=QTabWidget();box.addWidget(self.tabs,1)
        overview=QWidget();body=QVBoxLayout(overview)
        self.customer_counts=QLabel();body.addWidget(self.customer_counts)
        grid=QGridLayout();self.charts={}
        for i,(field,label) in enumerate((('profit_units','Lợi nhuận đã xác định / ngày (VND)'),('jobs','Số job kết thúc / ngày'),('customers','Lượt khách hàng / ngày'))):
            chart=DailyChart(field,label)
            chart.clicked.connect(lambda day,f=field:self.select_day(day,f))
            grid.addWidget(chart,i//2,i%2);self.charts[field]=chart
        self.pie=ReasonChart();grid.addWidget(self.pie,1,1);body.addLayout(grid)
        self.summary=table(['Người / Chat ID','Số job','Doanh thu (VND)','Chi phí đã biết (VND)','Lợi nhuận đã xác định (VND)','Chờ đối soát'])
        self.summary.setMinimumHeight(155);body.addWidget(self.summary)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(overview)
        self.tabs.addTab(scroll,'Biểu đồ')
        detail=QWidget();details=QVBoxLayout(detail)
        self.caption=QLabel('Lịch sử job theo tháng / người');details.addWidget(self.caption)
        self.history=table(['Người / Chat ID','Mã job','Mã mời','Trạng thái','Bean','Doanh thu','Chi phí','Lợi nhuận','Kết thúc'])
        details.addWidget(self.history)
        actions=QHBoxLayout()
        for label,callback in (('Trang trước',lambda:self.turn(-1)),('Trang sau',lambda:self.turn(1))):
            button=QPushButton(label);button.clicked.connect(callback);actions.addWidget(button)
        self.page_label=QLabel();actions.addWidget(self.page_label)
        export=QPushButton('Xuất CSV lịch sử');export.clicked.connect(lambda:window.send('history.export',callback=lambda r:window.save_text(r,'job_history.csv','CSV (*.csv)',bom=True)))
        actions.addWidget(export);details.addLayout(actions)
        self.tabs.addTab(detail,'Lịch sử job / Chi tiết')
        window.history=self.history
        self.month.dateChanged.connect(self.reset)
        self.owner.currentIndexChanged.connect(self.reset_owner)

    def turn(self,direction):
        new=self.offset+direction*100
        if 0<=new<getattr(self,'total',0):self.offset=new;self.load()

    def reset(self,*_):
        self.day=None;self.offset=0;self.load()

    def reset_owner(self,*_):
        self.offset=0;self.load()

    def all_days(self):
        self.day=None;self.offset=0;self.mode='jobs';self.load()

    def select_day(self,day,field):
        self.day=day;self.offset=0;self.mode='customers' if field=='customers' else 'jobs'
        self.tabs.setCurrentIndex(1);self.load()

    def load(self,*_):
        w=self.window
        if not w.client or not w._connected or w._closing:return
        if self.busy:self.pending=True;return
        self.busy=True
        payload=dict(month=self.month.date().toString('yyyy-MM'),offset=self.offset)
        if self.day:payload['day']=self.day
        if self.owner.currentData() is not None:payload['owner_id']=self.owner.currentData()
        client=w.client
        worker=w.task(lambda:client.command('statistics.get',payload),lambda r:self.render(r['result']) if w.client is client else None)
        def done():
            self.busy=False
            if self.pending:self.pending=False;self.load()
        worker.finished.connect(done)

    def render(self,result):
        from gui.remote_window import fill
        self.note.setText(result['note'])
        old=self.owner.currentData();self.owner.blockSignals(True);self.owner.clear();self.owner.addItem('Tất cả mọi người',None)
        for owner in result['owners']:self.owner.addItem(f"{owner['name']} ({owner['chat_id']})",owner['chat_id'])
        self.owner.setCurrentIndex(max(0,self.owner.findData(old)));self.owner.blockSignals(False)
        for chart in self.charts.values():chart.set_rows(result['days'])
        self.pie.set_rows(result['reasons']);self.pie.setMinimumHeight(max(260,60+46*len(result['reasons'])))
        c=result['customer_counts'];self.customer_counts.setText(f"Lượt khách: hôm nay {c['today']} · tuần này {c['week']} · tháng {result['month']}: {c['month']}")
        groups=[dict(person=f"{r['name']} ({r['chat_id']})",jobs=r['jobs'],revenue=money(r['revenue_units']),
            cost=money(r['cost_units']),profit=money(r['profit_units']),pending=r['pending_jobs']) for r in result['users']]
        fill(self.summary,groups,['person','jobs','revenue','cost','profit','pending'])
        customers=self.mode=='customers'
        source=result['customer_history'] if customers else result['history']
        rows=[]
        for r in source:
            rows.append(dict(person=f"{r['name']} ({r['chat_id']})",job_id=r['job_id'],code=r['code'],status=r['status'],
                beans=r['success_count']*100,revenue='' if customers else money(r['revenue_units']),
                cost='' if customers else money(r['cost_units']),profit='' if customers or r['profit_units'] is None else money(r['profit_units']),
                finished=r.get('finished_at') or r.get('finalized_at')))
        if not customers:
            ordered=[]
            for group in groups:
                ordered.extend(r for r in rows if r['person']==group['person'])
                ordered.append(dict(person='TỔNG '+group['person'],revenue=group['revenue'],cost=group['cost'],profit=group['profit']))
            rows=ordered
        fill(self.history,rows,['person','job_id','code','status','beans','revenue','cost','profit','finished'])
        self.total=result['customer_total'] if customers else result['total']
        self.caption.setText(('Mã mời / lượt khách' if customers else 'Job và tổng tài chính theo người')+' · '+(self.day or result['month']))
        self.page_label.setText(f"{min(self.offset+1,self.total)}–{min(self.offset+100,self.total)} / {self.total}")


def build_statistics(window):
    panel=StatisticsPanel(window)
    window.statistics_panel=panel
    return panel
