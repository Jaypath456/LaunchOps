import React, { useCallback, useMemo } from 'react';
import { Editor, Element as SlateElement, Transforms } from 'slate';
import {
  Plate,
  PlateContent,
  useEditorRef,
  usePlateEditor,
} from '@udecode/plate/react';
import './PlateEditor.css';

// Keep a default paragraph so the editor never starts completely empty
export const emptyDocument = [
  {
    type: 'paragraph',
    children: [{ text: '' }],
  },
];

function ToolbarButton({ active, onMouseDown, children, title }) {
  return (
    <button
      type="button"
      title={title}
      onMouseDown={onMouseDown}
      className={`toolbar-btn ${active ? 'active' : ''}`}
    >
      {children}
    </button>
  );
}

const LIST_TYPES = ['bulleted-list', 'numbered-list'];

function isMarkActive(editor, format) {
  const marks = Editor.marks(editor);
  return marks ? marks[format] === true : false;
}

function toggleMark(editor, format) {
  if (isMarkActive(editor, format)) {
    Editor.removeMark(editor, format);
  } else {
    Editor.addMark(editor, format, true);
  }
}

function isBlockActive(editor, format) {
  if (!editor.selection) return false;

  const [match] = Array.from(
    Editor.nodes(editor, {
      at: Editor.unhangRange(editor, editor.selection),
      match: (node) =>
        !Editor.isEditor(node) &&
        SlateElement.isElement(node) &&
        node.type === format,
    })
  );

  return !!match;
}

function toggleBlock(editor, format) {
  const active = isBlockActive(editor, format);
  const isList = LIST_TYPES.includes(format);

  // Prevent nested lists from getting messy
  Transforms.unwrapNodes(editor, {
    match: (node) =>
      !Editor.isEditor(node) &&
      SlateElement.isElement(node) &&
      LIST_TYPES.includes(node.type),
    split: true,
  });

  Transforms.setNodes(editor, {
    type: active ? 'paragraph' : isList ? 'list-item' : format,
  });

  if (!active && isList) {
    Transforms.wrapNodes(editor, {
      type: format,
      children: [],
    });
  }
}

function Toolbar() {
  const editor = useEditorRef();

  return (
    <div className="editor-toolbar">
      <ToolbarButton
        active={isMarkActive(editor, 'bold')}
        title="Bold"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleMark(editor, 'bold');
        }}
      >
        <b>B</b>
      </ToolbarButton>

      <ToolbarButton
        active={isMarkActive(editor, 'italic')}
        title="Italic"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleMark(editor, 'italic');
        }}
      >
        <i>I</i>
      </ToolbarButton>

      <ToolbarButton
        active={isMarkActive(editor, 'underline')}
        title="Underline"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleMark(editor, 'underline');
        }}
      >
        <u>U</u>
      </ToolbarButton>

      <ToolbarButton
        active={isMarkActive(editor, 'code')}
        title="Inline Code"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleMark(editor, 'code');
        }}
      >
        {'</>'}
      </ToolbarButton>

      <span className="toolbar-divider" />

      <ToolbarButton
        active={isBlockActive(editor, 'heading-one')}
        title="Heading 1"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleBlock(editor, 'heading-one');
        }}
      >
        H1
      </ToolbarButton>

      <ToolbarButton
        active={isBlockActive(editor, 'heading-two')}
        title="Heading 2"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleBlock(editor, 'heading-two');
        }}
      >
        H2
      </ToolbarButton>

      <span className="toolbar-divider" />

      <ToolbarButton
        active={isBlockActive(editor, 'bulleted-list')}
        title="Bullet List"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleBlock(editor, 'bulleted-list');
        }}
      >
        • List
      </ToolbarButton>

      <ToolbarButton
        active={isBlockActive(editor, 'numbered-list')}
        title="Numbered List"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleBlock(editor, 'numbered-list');
        }}
      >
        1. List
      </ToolbarButton>

      <ToolbarButton
        active={isBlockActive(editor, 'block-quote')}
        title="Quote"
        onMouseDown={(e) => {
          e.preventDefault();
          toggleBlock(editor, 'block-quote');
        }}
      >
        ❝
      </ToolbarButton>
    </div>
  );
}

function EditorElement({ attributes, children, element }) {
  switch (element.type) {
    case 'heading-one':
      return <h1 {...attributes}>{children}</h1>;

    case 'heading-two':
      return <h2 {...attributes}>{children}</h2>;

    case 'block-quote':
      return <blockquote {...attributes}>{children}</blockquote>;

    case 'bulleted-list':
      return <ul {...attributes}>{children}</ul>;

    case 'numbered-list':
      return <ol {...attributes}>{children}</ol>;

    case 'list-item':
      return <li {...attributes}>{children}</li>;

    default:
      return <p {...attributes}>{children}</p>;
  }
}

function EditorLeaf({ attributes, children, leaf }) {
  if (leaf.bold) {
    children = <strong>{children}</strong>;
  }

  if (leaf.italic) {
    children = <em>{children}</em>;
  }

  if (leaf.underline) {
    children = <u>{children}</u>;
  }

  if (leaf.code) {
    children = <code>{children}</code>;
  }

  return <span {...attributes}>{children}</span>;
}

export default function PlateEditor({
  value,
  onChange,
  readOnly = false,
}) {
  const initialValue = useMemo(() => {
    if (!value || (Array.isArray(value) && value.length === 0)) {
      return emptyDocument;
    }

    return value;
  }, [value]);

  const editor = usePlateEditor(
    {
      value: initialValue,
    },
    []
  );

  const renderElement = useCallback(
    (props) => <EditorElement {...props} />,
    []
  );

  const renderLeaf = useCallback(
    (props) => <EditorLeaf {...props} />,
    []
  );

  if (readOnly) {
    return (
      <Plate editor={editor} readOnly>
        <div className="editor-readonly">
          <PlateContent
            renderElement={renderElement}
            renderLeaf={renderLeaf}
            readOnly
          />
        </div>
      </Plate>
    );
  }

  return (
    <Plate
      editor={editor}
      onValueChange={({ value: newValue }) => {
        onChange?.(newValue);
      }}
    >
      <div className="editor-wrapper">
        <Toolbar />

        <div className="editor-body">
          <PlateContent
            renderElement={renderElement}
            renderLeaf={renderLeaf}
            placeholder="chapter content..."
            spellCheck
            autoFocus
          />
        </div>
      </div>
    </Plate>
  );
}