from rich.tree import Tree


def _add_dir_contents_to_tree(parent_branch: Tree, tree_node: dict):
    """Add directory contents to tree branch"""
    items = []

    # Add files
    if "_files" in tree_node:
        for filename, file in tree_node["_files"]:
            items.append(("file", filename, file))

    # Add subdirectories
    for key, subtree in tree_node.items():
        if key != "_files":
            items.append(("dir", key, subtree))

    # Sort items - dirs first, then files
    items.sort(key=lambda x: (x[0] != "dir", x[1]))

    for item_type, name, data in items:
        if item_type == "file":
            parent_branch.add(f"{name} ({_format_size(data.size)})")
        else:
            dir_size = _calculate_dir_size(data)
            dir_branch = parent_branch.add(f"{name}/ ({_format_size(dir_size)})")
            _add_dir_contents_to_tree(dir_branch, data)


def _add_files_to_tree(parent_branch: Tree, files: list, max_files: int = 0):
    """Add files to a Rich tree branch with proper directory structure"""
    if not files:
        return

    # Apply file limit if specified
    files_to_show = files
    if max_files > 0:
        files_to_show = files[:max_files]

    # Group files by their directory structure
    tree = {}
    single_files = []

    for file in files_to_show:
        if len(file.path.parts) == 1:
            single_files.append(file)
        else:
            current = tree
            for part in file.path.parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Store the file at the leaf
            if "_files" not in current:
                current["_files"] = []
            current["_files"].append((file.path.parts[-1], file))

    # Add single files first
    for file in single_files:
        parent_branch.add(f"{file.path.name} ({_format_size(file.size)})")

    # Add directories
    for dir_name in sorted(tree.keys()):
        dir_size = _calculate_dir_size(tree[dir_name])
        dir_branch = parent_branch.add(f"{dir_name}/ ({_format_size(dir_size)})")
        _add_dir_contents_to_tree(dir_branch, tree[dir_name])

    # Show "... and N more" if files were truncated
    if 0 < max_files < len(files):
        remaining = len(files) - max_files
        parent_branch.add(f"[dim]... and {remaining} more files[/dim]")


def _format_size(size_bytes: int) -> str:
    """Format size in human readable format"""
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_idx = 0

    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1

    if size == int(size):
        return f"{int(size)} {units[unit_idx]}"
    return f"{size:.2f} {units[unit_idx]}"


def _calculate_dir_size(tree_node: dict) -> int:
    """Calculate total size of directory"""
    total = 0

    if "_files" in tree_node:
        for _, file in tree_node["_files"]:
            total += file.size

    for key, subtree in tree_node.items():
        if key != "_files":
            total += _calculate_dir_size(subtree)

    return total
