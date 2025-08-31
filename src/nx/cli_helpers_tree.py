from __future__ import annotations

from dataclasses import dataclass, field

from rich.tree import Tree

from .nx import File


@dataclass
class TreeNode:
    """Represents a directory node in the file tree structure"""

    files: list[tuple[str, File]] = field(default_factory=list)
    subdirs: dict[str, TreeNode] = field(default_factory=dict)

    def add_file(self, filename: str, file: File) -> None:
        """Add a file to this directory node"""
        self.files.append((filename, file))

    def get_or_create_subdir(self, name: str) -> TreeNode:
        """Get or create a subdirectory"""
        if name not in self.subdirs:
            self.subdirs[name] = TreeNode()
        return self.subdirs[name]


def _add_dir_contents_to_tree(parent_branch: Tree, tree_node: TreeNode) -> None:
    """Add directory contents to tree branch"""
    # Add directories first (sorted)
    for dir_name in sorted(tree_node.subdirs.keys()):
        subtree = tree_node.subdirs[dir_name]
        dir_size = _calculate_dir_size(subtree)
        dir_branch = parent_branch.add(f"{dir_name}/ ({_format_size(dir_size)})")
        _add_dir_contents_to_tree(dir_branch, subtree)

    # Add files second (sorted)
    for filename, file in sorted(tree_node.files):
        parent_branch.add(f"{filename} ({_format_size(file.size)})")


def _add_files_to_tree(
    parent_branch: Tree, files: list[File], max_files: int = 0
) -> None:
    """Add files to a Rich tree branch with proper directory structure"""
    if not files:
        return

    # Apply file limit if specified
    files_to_show = files
    if max_files > 0:
        files_to_show = files[:max_files]

    # Group files by their directory structure
    root = TreeNode()
    single_files = []

    for file in files_to_show:
        if len(file.path.parts) == 1:
            single_files.append(file)
        else:
            current = root
            for part in file.path.parts[:-1]:
                current = current.get_or_create_subdir(part)

            # Store the file at the leaf
            current.add_file(file.path.parts[-1], file)

    # Add single files first
    for file in single_files:
        parent_branch.add(f"{file.path.name} ({_format_size(file.size)})")

    # Add directories
    for dir_name in sorted(root.subdirs.keys()):
        subdir = root.subdirs[dir_name]
        dir_size = _calculate_dir_size(subdir)
        dir_branch = parent_branch.add(f"{dir_name}/ ({_format_size(dir_size)})")
        _add_dir_contents_to_tree(dir_branch, subdir)

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


def _calculate_dir_size(tree_node: TreeNode) -> int:
    """Calculate total size of directory"""
    total = 0

    for _, file in tree_node.files:
        total += file.size

    for subtree in tree_node.subdirs.values():
        total += _calculate_dir_size(subtree)

    return total
