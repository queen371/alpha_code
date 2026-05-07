#!/usr/bin/env python3
"""Dev utility: walk `alpha/tools/` and print a summary of registered tools.

Counts `register_tool(...)` calls per file and groups by category. Helpful
for verifying tool surface after refactors. Not imported by the runtime.
Run via: `python analyze_tools.py`.
"""
import os
import re
import glob

def analyze_tools():
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alpha", "tools")
    
    # Padrões regex
    register_pattern = r'register_tool\('
    category_pattern = r'category\s*=\s*["\']([^"\']+)["\']'
    name_pattern = r'name\s*=\s*["\']([^"\']+)["\']'
    
    total_tools = 0
    categories = {}
    tools_list = []
    
    # Analisar cada arquivo Python
    for py_file in glob.glob(f"{tools_dir}/*.py"):
        if "__init__.py" in py_file or "__pycache__" in py_file:
            continue
            
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Encontrar todas as chamadas register_tool
                register_matches = list(re.finditer(register_pattern, content))
                
                for match in register_matches:
                    total_tools += 1
                    
                    # Extrair o bloco após register_tool
                    start_pos = match.start()
                    # Encontrar o final do bloco (próximo parêntese de fechamento)
                    # Vamos buscar um trecho razoável após o início
                    block_end = min(start_pos + 2000, len(content))
                    block = content[start_pos:block_end]
                    
                    # Extrair nome
                    name_match = re.search(name_pattern, block)
                    tool_name = name_match.group(1) if name_match else "unknown"
                    
                    # Extrair categoria
                    category_match = re.search(category_pattern, block)
                    category = category_match.group(1) if category_match else "general"
                    
                    # Registrar
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(tool_name)
                    tools_list.append({"name": tool_name, "category": category})
                    
        except Exception as e:
            print(f"Erro ao processar {py_file}: {e}")
    
    return total_tools, categories, tools_list

if __name__ == "__main__":
    total, categories_dict, tools = analyze_tools()
    
    print(f"Total de tools registradas: {total}")
    print(f"\nCategorias encontradas ({len(categories_dict)}):")
    
    for category in sorted(categories_dict.keys()):
        tools_in_category = categories_dict[category]
        print(f"\n- {category}: {len(tools_in_category)} tools")
        for tool in sorted(tools_in_category):
            print(f"  • {tool}")
    
    print(f"\nResumo:")
    for category in sorted(categories_dict.keys()):
        print(f"- {category}: {len(categories_dict[category])} tools")