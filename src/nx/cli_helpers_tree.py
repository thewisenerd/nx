from __future__ import annotations

from dataclasses import dataclass, field

from rich.tree import Tree

from .nx import File


@dataclass
class TreeNode:
    """Represents a directory node in the file tree structure"""

    size: int = 0
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


def _build_file_tree(files: list[File]) -> TreeNode:
    root = TreeNode()

    for file in files:
        current = root
        current.size += file.size

        for part in file.path.parts[:-1]:
            current = current.get_or_create_subdir(part)
            current.size += file.size

        current.add_file(file.path.parts[-1], file)

    return root


def _add_dir_contents_to_tree(
    parent_branch: Tree, visible_node: TreeNode, complete_node: TreeNode
) -> None:
    """Add visible directory contents with complete directory sizes"""
    # Add directories first (sorted)
    for dir_name in sorted(visible_node.subdirs):
        visible_subtree = visible_node.subdirs[dir_name]
        complete_subtree = complete_node.subdirs[dir_name]
        dir_branch = parent_branch.add(
            f"{dir_name}/ ({_format_size(complete_subtree.size)})"
        )
        _add_dir_contents_to_tree(dir_branch, visible_subtree, complete_subtree)

    # Add files second (sorted)
    for filename, file in sorted(visible_node.files):
        parent_branch.add(f"{filename} ({_format_size(file.size)})")


def _add_files_to_tree(
    parent_branch: Tree, files: list[File], max_files: int = 0
) -> None:
    """Add files to a Rich tree branch with proper directory structure"""
    if not files:
        return

    complete_tree = _build_file_tree(files)
    visible_files = files[:max_files] if max_files > 0 else files
    visible_tree = _build_file_tree(visible_files)

    # Add single files first
    for filename, file in visible_tree.files:
        parent_branch.add(f"{filename} ({_format_size(file.size)})")

    # Add directories
    for dir_name in sorted(visible_tree.subdirs):
        visible_subtree = visible_tree.subdirs[dir_name]
        complete_subtree = complete_tree.subdirs[dir_name]
        dir_branch = parent_branch.add(
            f"{dir_name}/ ({_format_size(complete_subtree.size)})"
        )
        _add_dir_contents_to_tree(dir_branch, visible_subtree, complete_subtree)

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
