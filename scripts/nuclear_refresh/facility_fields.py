from html.parser import HTMLParser
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):return ''.join(c if isinstance(c,str) else c.text() for c in self.children).strip()
    def all(self):
        yield self
        for c in self.children:
            if isinstance(c,Node):yield from c.all()
class Tree(HTMLParser):
    def __init__(self):super().__init__();self.root=Node();self.stack=[self.root]
    def handle_starttag(self,t,a):
        n=Node(t,a);self.stack[-1].children.append(n)
        if t not in VOID:self.stack.append(n)
    def handle_endtag(self,t):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==t:self.stack=self.stack[:i];break
    def handle_data(self,d):self.stack[-1].children.append(d)
def fields(html):
    tree=Tree();tree.feed(html);out={}
    for n in tree.root.all():
        if 'mud-input-control-input-container' not in n.attrs.get('class','').split():continue
        nodes=list(n.all());labels=[x for x in nodes if x.tag=='label'];values=[x for x in nodes if 'mud-input-slot' in x.attrs.get('class','').split()]
        if len(labels)==1 and len(values)==1:out[labels[0].text()]=values[0].text() or None
    return out
ISO=dict(zip(['Albania','Argentina','Armenia','Australia','Austria','Belarus','Belgium','Bosnia and Herzegovina','Brazil','Bulgaria','Burkina Faso','Canada','Chile','China','Czech Republic','Denmark','Egypt','Estonia','Finland','France','Gabon','Georgia','Germany','Hungary','India','Indonesia','Israel','Italy','Japan','Kazakhstan','Korea, Republic of','Kyrgyzstan','Lithuania','Luxembourg','Malawi','Mexico','Mongolia','Morocco','Namibia','Netherlands, Kingdom of the','Niger','Norway','Portugal','Romania','Russian Federation','Serbia','Slovakia','Slovenia','South Africa','Spain','Sweden','Switzerland','Syrian Arab Republic','Tajikistan','Thailand','Tunisia','Türkiye','Ukraine','United Kingdom','United States of America','Uzbekistan'],['al','ar','am','au','at','by','be','ba','br','bg','bf','ca','cl','cn','cz','dk','eg','ee','fi','fr','ga','ge','de','hu','in','id','il','it','jp','kz','kr','kg','lt','lu','mw','mx','mn','ma','na','nl','ne','no','pt','ro','ru','rs','sk','si','za','es','se','ch','sy','tj','th','tn','tr','ua','gb','us','uz']))

class Text(HTMLParser):
    def __init__(self):super().__init__();self.out=[];self.skip=0
    def handle_starttag(self,t,a):
        if t in ('script','style'):self.skip+=1
    def handle_endtag(self,t):
        if t in ('script','style'):self.skip-=1
    def handle_data(self,d):
        if not self.skip and d.strip():self.out.append(d.strip())

ISO['Greece']='gr'
