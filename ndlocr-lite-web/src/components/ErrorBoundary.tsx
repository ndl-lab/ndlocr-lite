import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div className="max-w-lg mx-auto mt-16 p-6 bg-red-50 border border-red-200 rounded-xl text-center space-y-3">
          <p className="text-red-700 font-semibold">Unexpected error</p>
          <pre className="text-xs text-red-600 overflow-auto whitespace-pre-wrap">{error.message}</pre>
          <button
            className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 text-sm"
            onClick={() => this.setState({ error: null })}
          >
            Dismiss
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
