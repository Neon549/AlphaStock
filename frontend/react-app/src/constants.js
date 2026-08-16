export const MODELS = [
  { value: 'smart', label: 'DeepSeek-R1 · 均衡（默认）' },
  { value: 'strong', label: 'DeepSeek-R1 · 严格推理' },
  { value: 'fast', label: 'DeepSeek-V3 · 快速' },
]

export const STRATEGIES = [
  { value: 'kdj_oversold', label: 'KDJ 超卖反弹' },
  { value: 'j_extreme', label: 'J线极值反转' },
  { value: 'rsi', label: 'RSI 相对强弱' },
  { value: 'boll', label: '布林带突破' },
  { value: 'kdj_macd', label: 'KDJ+MACD 共振' },
]

export const SECTORS = [
  'CPO光模块','PCB','半导体','AI算力','造船','军工','低空经济','新能源储能',
  '有色金属','煤炭能源','化工','医药','消费白酒','银行','机器人','汽车整车',
  '锂电池','光伏太阳能','风电','电力','钢铁','建筑材料','农业','食品饮料',
  '医疗器械','证券','保险','航空航天','传媒娱乐','物流快递','消费电子',
  '云计算软件','网络安全','工业自动化','房地产','化工材料','稀土特材',
  '银行扩充','半导体扩充','AI大模型','汽车零部件扩充','医药生物扩充',
  '新能源车产业链','消费白酒扩充','银行扩充2','地产链','消费零售',
  '军工扩充','数字经济','稀有金属','电子元器件','通信设备','机械设备',
  '纺织服装','旅游酒店','教育','环保','传统能源','黄金珠宝','游戏',
  '农化','水务','大金融','基建交通','医药扩充2','智能制造','半导体材料',
  '消费升级','新材料','零售电商',
]

export const SCAN_STRATEGIES = [
  { value: 'all', label: '全部信号（KDJ超卖 + KDJ金叉）' },
  { value: 'oversold', label: 'KDJ 超卖反弹（J值极低）' },
  { value: 'cross', label: 'KDJ 金叉（K线上穿J线）' },
]
