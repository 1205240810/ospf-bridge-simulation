import streamlit as st
import streamlit.components.v1 as components
import yaml
import os
import subprocess
import time
import base64
import pexpect  # 【新增】用于实现后台幽灵探针
from pyvis.network import Network
st.set_page_config(page_title="网络仿真控制台", layout="wide")
st.title("🌐 网络仿真控制台 (Web UI)")

# ==========================================================
# 【暗号本与探针引擎：负责真实的 Ping 测试】
# ==========================================================
PORT_MAP = {
    "R1": 5001, "R2": 5002, "R3": 5003,
    "PC1": 5004, "PC2": 5005,
    "S1": 6001, "S2": 6002
}

def run_real_ping(node_name, target_ip):
    port = PORT_MAP.get(node_name)
    if not port:
        return f"❌ 找不到节点 {node_name} 的端口号，请检查 PORT_MAP 字典！"
    
    try:
        child = pexpect.spawn(f'telnet 127.0.0.1 {port}', encoding='utf-8', timeout=10)
        
        # PC 节点的处理逻辑
        if node_name.startswith("PC"):
            child.sendline('')
            index = child.expect(['login:', r'\$', pexpect.TIMEOUT])
            if index == 0:
                child.sendline('cirros')
                child.expect('Password:')
                child.sendline('gocubsgo')
                time.sleep(3) # 等待 CirrOS 吐完报错
                child.sendline('')
                child.expect(r'\$')
            elif index == 2:
                return "❌ 登录超时！PC 可能处于关机或死机状态。"
            
            child.sendline(f'ping -c 4 {target_ip}')
            child.expect(r'\$ ', timeout=15)
            result = child.before
            child.close()
            return result.strip()
            
        # 路由器/交换机 节点的处理逻辑
        else:
            child.sendline('')
            child.expect([r'<.*?>', r'\[.*?\]'], timeout=5)
            child.sendline(f'ping {target_ip}')
            child.expect([r'<.*?>', r'\[.*?\]'], timeout=15)
            result = child.before
            child.close()
            return result.strip()
            
    except Exception as e:
        return f"❌ 探针执行异常，可能是设备未开机或被占用：\n{str(e)}"

# ==========================================================
# 【核心黑科技：直接写死的极简线条图标，无须本地文件】
# ==========================================================
ROUTER_SVG = """
<svg width="100px" height="100px" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <ellipse cx="50" cy="30" rx="40" ry="20" style="fill:none;stroke:white;stroke-width:4"/>
  <rect x="10" y="30" width="80" height="40" style="fill:none;stroke:white;stroke-width:4"/>
  <ellipse cx="50" cy="70" rx="40" ry="20" style="fill:none;stroke:white;stroke-width:4"/>
  <path d="M50 15 L50 25 M45 20 L50 25 L55 20" style="fill:none;stroke:white;stroke-width:3"/>
  <path d="M50 45 L50 35 M45 40 L50 35 L55 40" style="fill:none;stroke:white;stroke-width:3"/>
  <path d="M35 30 L25 30 M30 25 L25 30 L30 35" style="fill:none;stroke:white;stroke-width:3"/>
  <path d="M65 30 L75 30 M70 25 L75 30 L70 35" style="fill:none;stroke:white;stroke-width:3"/>
</svg>
"""
SWITCH_SVG = """
<svg width="100px" height="100px" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="20" width="80" height="60" style="fill:none;stroke:white;stroke-width:4"/>
  <path d="M30 40 L70 40 M60 30 L70 40 L60 50" style="fill:none;stroke:white;stroke-width:3"/>
  <path d="M70 60 L30 60 M40 50 L30 60 L40 70" style="fill:none;stroke:white;stroke-width:3"/>
</svg>
"""
PC_SVG = """
<svg width="100px" height="100px" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <rect x="15" y="15" width="70" height="50" rx="5" style="fill:none;stroke:white;stroke-width:4"/>
  <rect x="40" y="65" width="20" height="10" style="fill:none;stroke:white;stroke-width:4"/>
  <line x1="10" y1="85" x2="90" y2="85" style="stroke:white;stroke-width:4;stroke-linecap:round"/>
</svg>
"""
CLOUD_SVG = """
<svg width="100px" height="100px" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <path d="M25,60 a20,20 0 0,1 0,-40 a20,20 0 0,1 35,-10 a20,20 0 0,1 30,10 a20,20 0 0,1 0,40 Z" style="fill:none;stroke:white;stroke-width:4"/>
</svg>
"""

def svg_to_base64(svg_string):
    b64_str = base64.b64encode(svg_string.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{b64_str}"

skin_router = svg_to_base64(ROUTER_SVG)
skin_switch = svg_to_base64(SWITCH_SVG)
skin_pc = svg_to_base64(PC_SVG)
skin_cloud = svg_to_base64(CLOUD_SVG)


# ==========================================================
# --- 側邊欄 ---
# ==========================================================
st.sidebar.header("⚙️ 引擎控制面板")
yaml_path = st.sidebar.text_input("拓扑文件路径", value="topology.yaml")

st.sidebar.markdown("---")
# 【修复 1：拉起指令挂载行车记录仪】
if st.sidebar.button("🚀 1. 拉起底层网络 (main.py load)", type="primary"):
    with st.spinner("已向后台发送拉起指令，设备正在异步启动..."):
        log_file = open("main_run.log", "w")
        subprocess.Popen(
            ["python3", "main.py", "load", yaml_path], 
            stdout=log_file, 
            stderr=subprocess.STDOUT, 
            cwd=os.getcwd()
        )
        time.sleep(2)
        st.sidebar.success("✅ 拉起指令已发送！(如果失败，请查看同目录的 main_run.log)")

if st.sidebar.button("⚡ 2. 自动化配置 (auto_config.py)", type="primary"):
    st.sidebar.info("开始执行配置，请看下方实时日志 👇")
    log_box = st.sidebar.empty() 
    process = subprocess.Popen(
        ["python3", "-u", "auto_config.py"], 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True,
        cwd=os.getcwd()
    )
    logs = ""
    for line in iter(process.stdout.readline, ''):
        logs += line
        log_box.code(logs, language="bash")
    process.stdout.close()
    process.wait()
    if process.returncode == 0:
        st.sidebar.success("✅ OSPF 与 IP 配置完毕！")
    else:
        st.sidebar.error(f"❌ 脚本执行失败或超时！退出码: {process.returncode}")

# 【修复 3：核弹清场加入绝对路径确保清理】
if st.sidebar.button("🧹 3. 核弹清场 (main.py clean)"):
    with st.spinner("清理中..."):
        subprocess.run(["python3", "main.py", "clean"], capture_output=True, text=True, cwd=os.getcwd())
    st.sidebar.success("✅ 环境已清空！")


# ==========================================================
# --- 主界面 ---
# ==========================================================
col1, col2 = st.columns([1.2, 0.8]) 

with col1:
    st.subheader("🕸️ 实时物理网络拓扑图")
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            topo = yaml.safe_load(f)
            
            net = Network(height="450px", width="100%", bgcolor="#0e1117", font_color="white", cdn_resources='remote')
            net.repulsion(node_distance=180, spring_length=200) 
            
            for dev in topo.get('devices', []):
                dev_name = dev['name']
                if dev_name.startswith("R") or dev['type'] == 'ne40':
                    current_skin = skin_router
                    size = 35
                elif dev_name.startswith("S") or 'switch' in dev['type']:
                    current_skin = skin_switch
                    size = 35
                elif dev_name.startswith("PC"):
                    current_skin = skin_pc
                    size = 30
                else:
                    current_skin = skin_pc 
                    size = 30
                net.add_node(dev_name, label=dev_name, title=f"类型: {dev['type']}", shape="image", image=current_skin, size=size)
                
            for link_data in topo.get('links', []):
                if isinstance(link_data, dict):
                    link = link_data.get("endpoints", [])
                else:
                    link = link_data

                if len(link) == 3: node_a, node_b = link[0], link[1]
                elif len(link) == 4: node_a, node_b = link[0], link[2]
                else: continue 

                if node_a == "CLOUD" or node_b == "CLOUD":
                     net.add_node("CLOUD", label="外部网", shape="image", image=skin_cloud, size=45)

                try:
                    net.add_edge(node_a, node_b, color="#888888", width=2)
                except Exception: pass
            
            net.save_graph("topo_map.html")
            with open("topo_map.html", 'r', encoding='utf-8') as HtmlFile:
                source_code = HtmlFile.read() 
            components.html(source_code, height=470)
            
    except Exception as e:
        st.error(f"拓扑解析失败: {e}")

with col2:
    st.subheader("⚡ 连通性测试舱 (Ping)")
    st.info("💡 提示：点击后将实时连入底层设备发起真实的 ICMP 物理探测。")
    
    # 【新增：打通真实的 Ping 探针 UI】
    source_node = st.selectbox("选择发起探测的源节点", list(PORT_MAP.keys()))
    target_ip = st.text_input("输入目标 IP 地址", value="110.1.1.1") 
    
    if st.button("🎯 发起真实 Ping 测试", type="primary"):
        with st.spinner(f"正在驱动 {source_node} 向 {target_ip} 发射探测包，请等待 3-5 秒..."):
            ping_result = run_real_ping(source_node, target_ip)
            
            if "❌" in ping_result:
                st.error("执行失败，请查看下方报错信息：")
                st.code(ping_result, language="bash")
            else:
                st.success(f"✅ {source_node} 探测 {target_ip} 完毕！真实物理回显如下：")
                st.code(ping_result, language="bash")