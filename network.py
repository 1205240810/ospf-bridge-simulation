from driver import run_cmd

def apply_link_emulation(interface_name, emulation_params):
    """
    解析字典参数，并调用 Linux TC 对指定虚拟网卡进行流量整形
    """
    if not emulation_params:
        return

    delay = emulation_params.get("delay")
    loss = emulation_params.get("loss")
    rate = emulation_params.get("rate")

    # 【修复1】加上 sudo，确保 TC 规则有绝对权限下发
    cmd = f"sudo tc qdisc add dev {interface_name} root netem"
    
    if delay:
        cmd += f" delay {delay}"
    if loss:
        cmd += f" loss {loss}"
    if rate:
        cmd += f" rate {rate}"

    print(f"🚥 [链路仿真] 正在为网口 {interface_name} 注入劣化参数: {emulation_params}")
    run_cmd(cmd)

class Bridge:
    """
    使用 Open vSwitch 实现的网桥
    """
    def __init__(self, name):
        self.name = name

    def create(self):
        run_cmd(f"ovs-vsctl --may-exist add-br {self.name}")
        run_cmd(f"ip link set dev {self.name} up")

    def destroy(self):
        run_cmd(f"ovs-vsctl --if-exists del-br {self.name}")

    def add_port(self, interface_name):
        run_cmd(f"ovs-vsctl --may-exist add-port {self.name} {interface_name}")

    def del_port(self, interface_name):
        run_cmd(f"ovs-vsctl --if-exists del-port {self.name} {interface_name}")
        
    def bind_physical_interface(self, physical_if_name):
        print(f"🔗 OVS 正在桥接物理网卡: {physical_if_name} -> {self.name}")
        run_cmd(f"ip link set dev {physical_if_name} up")
        self.add_port(physical_if_name)


class TapInterface:
    def __init__(self, name):
        self.name = name
        self.current_bridge = None

    def create(self):
        run_cmd(f"ip tuntap add dev {self.name} mode tap")
        run_cmd(f"ip link set dev {self.name} up")
        run_cmd(f"ip link set dev {self.name} promisc on")

    def destroy(self):
        self.unplug()
        run_cmd(f"ip link del {self.name} 2>/dev/null")

    def plug_into(self, bridge: Bridge, emulation_params=None):
        """插线操作，并支持链路仿真参数注入"""
        self.unplug()
        
        bridge.add_port(self.name)
        
        self.current_bridge = bridge
        print(f"🔗 [连接成功] {self.name} <---> {bridge.name} (OVS)")

        # 在网卡插到 OVS 并就绪后，下发物理层劣化命令
        if emulation_params:
            apply_link_emulation(self.name, emulation_params)

    def unplug(self):
        """拔线操作"""
        if self.current_bridge:
            self.current_bridge.del_port(self.name)
            print(f"🔌 [断开连接] {self.name} X {self.current_bridge.name}")
            self.current_bridge = None


def create_veth_link(bridge_a_name, bridge_b_name, emulation_params=None):
    """
    创建 veth pair 连接两个 OVS 网桥，并支持链路仿真参数注入
    """
    import random
    suffix = random.randint(1000, 9999)
    veth_a = f"veth_a_{suffix}"
    veth_b = f"veth_b_{suffix}"

    print(f"🔗 [OVS级联] 正在连接: {bridge_a_name} <==> {bridge_b_name}")

    # 1. 创建 Linux veth pair
    run_cmd(f"ip link add {veth_a} type veth peer name {veth_b}")
    run_cmd(f"ip link set dev {veth_a} up")
    run_cmd(f"ip link set dev {veth_b} up")
    
    # 2. 【修复2】先插线！让 OVS 完成接管和初始化
    run_cmd(f"ovs-vsctl --may-exist add-port {bridge_a_name} {veth_a}")
    run_cmd(f"ovs-vsctl --may-exist add-port {bridge_b_name} {veth_b}")

    # 3. 【修复3】插线完毕后，再下发物理层劣化命令！
    # 并且只给 veth_a 下发，防止双向叠加导致延迟翻倍
    if emulation_params:
        apply_link_emulation(veth_a, emulation_params)