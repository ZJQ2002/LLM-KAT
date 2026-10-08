import ast
import operator
from decimal import Decimal


def safe_calculate(expression):
    if len(expression) > 256:
        raise ValueError("表达式过长")
    tree = ast.parse(expression, mode="eval")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    def walk(node, depth=0):
        if depth > 20:
            raise ValueError("表达式过深")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = Decimal(str(node.value))
            if not value.is_finite() or abs(value) > Decimal("1e15"):
                raise ValueError("数值超限")
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth + 1)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in operations:
            left, right = walk(node.left, depth + 1), walk(node.right, depth + 1)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("零分母必须标记信息不足，不能默认为零")
            return operations[type(node.op)](left, right)
        raise ValueError("仅允许数值及 + - * / 运算")
    return walk(tree.body)

