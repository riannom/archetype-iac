import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  PageLayout,
  PageContainer,
  PageTitle,
  EmptyState,
  LoadingState,
  ErrorState,
} from "./PageLayout";

describe("PageLayout", () => {
  describe("rendering", () => {
    it("renders children", () => {
      render(<PageLayout>Page Content</PageLayout>);
      expect(screen.getByText("Page Content")).toBeInTheDocument();
    });

    it("renders header when provided", () => {
      render(
        <PageLayout header={<div>Header</div>}>Content</PageLayout>
      );
      expect(screen.getByText("Header")).toBeInTheDocument();
    });

    it("renders footer when provided", () => {
      render(
        <PageLayout footer={<div>Footer</div>}>Content</PageLayout>
      );
      expect(screen.getByText("Footer")).toBeInTheDocument();
    });

    it("renders all sections together", () => {
      render(
        <PageLayout
          header={<div>Header</div>}
          footer={<div>Footer</div>}
        >
          Main Content
        </PageLayout>
      );
      expect(screen.getByText("Header")).toBeInTheDocument();
      expect(screen.getByText("Main Content")).toBeInTheDocument();
      expect(screen.getByText("Footer")).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<PageLayout className="custom-layout">Content</PageLayout>);
      const layout = document.querySelector(".custom-layout");
      expect(layout).toBeInTheDocument();
    });

    it("has min-h-screen class", () => {
      render(<PageLayout>Content</PageLayout>);
      const layout = document.querySelector(".min-h-screen");
      expect(layout).toBeInTheDocument();
    });

    it("has flex-col class", () => {
      render(<PageLayout>Content</PageLayout>);
      const layout = document.querySelector(".flex-col");
      expect(layout).toBeInTheDocument();
    });
  });
});

describe("PageContainer", () => {
  describe("rendering", () => {
    it("renders children", () => {
      render(<PageContainer>Container Content</PageContainer>);
      expect(screen.getByText("Container Content")).toBeInTheDocument();
    });
  });

  describe("maxWidth", () => {
    const maxWidths = ["sm", "md", "lg", "xl", "2xl", "7xl", "full"] as const;

    maxWidths.forEach((maxWidth) => {
      it(`renders with maxWidth ${maxWidth}`, () => {
        render(<PageContainer maxWidth={maxWidth}>Content</PageContainer>);
        expect(screen.getByText("Content")).toBeInTheDocument();
      });
    });

    it("uses 7xl maxWidth by default", () => {
      render(<PageContainer>Default</PageContainer>);
      const container = document.querySelector(".max-w-7xl");
      expect(container).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<PageContainer className="custom-container">Content</PageContainer>);
      const container = document.querySelector(".custom-container");
      expect(container).toBeInTheDocument();
    });

    it("has mx-auto class", () => {
      render(<PageContainer>Content</PageContainer>);
      const container = document.querySelector(".mx-auto");
      expect(container).toBeInTheDocument();
    });
  });
});

describe("PageTitle", () => {
  describe("rendering", () => {
    it("renders title", () => {
      render(<PageTitle title="Page Title" />);
      expect(screen.getByText("Page Title")).toBeInTheDocument();
    });

    it("renders description when provided", () => {
      render(<PageTitle title="Title" description="Page description" />);
      expect(screen.getByText("Page description")).toBeInTheDocument();
    });

    it("renders actions when provided", () => {
      render(
        <PageTitle
          title="Title"
          actions={<button>Action Button</button>}
        />
      );
      expect(screen.getByText("Action Button")).toBeInTheDocument();
    });

    it("does not render description when not provided", () => {
      render(<PageTitle title="Title" />);
      const heading = screen.getByRole("heading");
      expect(heading).toHaveTextContent("Title");
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<PageTitle title="Title" className="custom-title" />);
      const titleContainer = document.querySelector(".custom-title");
      expect(titleContainer).toBeInTheDocument();
    });

    it("has mb-8 class", () => {
      render(<PageTitle title="Title" />);
      const container = document.querySelector(".mb-8");
      expect(container).toBeInTheDocument();
    });
  });

  describe("heading", () => {
    it("renders h2 heading", () => {
      render(<PageTitle title="Title" />);
      expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Title");
    });
  });
});

describe("EmptyState", () => {
  describe("rendering", () => {
    it("renders title", () => {
      render(<EmptyState title="No Items" />);
      expect(screen.getByText("No Items")).toBeInTheDocument();
    });

    it("renders description when provided", () => {
      render(<EmptyState title="No Items" description="Add some items to get started" />);
      expect(screen.getByText("Add some items to get started")).toBeInTheDocument();
    });

    it("renders action when provided", () => {
      render(
        <EmptyState
          title="No Items"
          action={<button>Add Item</button>}
        />
      );
      expect(screen.getByText("Add Item")).toBeInTheDocument();
    });

    it("renders default icon", () => {
      render(<EmptyState title="Empty" />);
      const icon = document.querySelector("i.fa-inbox");
      expect(icon).toBeInTheDocument();
    });

    it("renders custom icon", () => {
      render(<EmptyState title="Empty" icon="fa-solid fa-folder" />);
      const icon = document.querySelector("i.fa-folder");
      expect(icon).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<EmptyState title="Empty" className="custom-empty" />);
      const container = document.querySelector(".custom-empty");
      expect(container).toBeInTheDocument();
    });

    it("has dashed border", () => {
      render(<EmptyState title="Empty" />);
      const container = document.querySelector(".border-dashed");
      expect(container).toBeInTheDocument();
    });

    it("has rounded corners", () => {
      render(<EmptyState title="Empty" />);
      const container = document.querySelector(".rounded-3xl");
      expect(container).toBeInTheDocument();
    });
  });
});

describe("LoadingState", () => {
  describe("rendering", () => {
    it("renders default loading message", () => {
      render(<LoadingState />);
      expect(screen.getByText("Loading...")).toBeInTheDocument();
    });

    it("renders custom message", () => {
      render(<LoadingState message="Fetching data..." />);
      expect(screen.getByText("Fetching data...")).toBeInTheDocument();
    });

    it("renders spinner icon", () => {
      render(<LoadingState />);
      const spinner = document.querySelector("i.fa-spinner");
      expect(spinner).toBeInTheDocument();
    });

    it("spinner has spin animation", () => {
      render(<LoadingState />);
      const spinner = document.querySelector("i.fa-spin");
      expect(spinner).toBeInTheDocument();
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<LoadingState className="custom-loading" />);
      const container = document.querySelector(".custom-loading");
      expect(container).toBeInTheDocument();
    });

    it("has center alignment", () => {
      render(<LoadingState />);
      const container = document.querySelector(".justify-center");
      expect(container).toBeInTheDocument();
    });
  });
});

describe("ErrorState", () => {
  describe("rendering", () => {
    it("renders error message", () => {
      render(<ErrorState message="Something went wrong" />);
      expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    });

    it("renders error icon", () => {
      render(<ErrorState message="Error" />);
      const icon = document.querySelector("i.fa-exclamation-circle");
      expect(icon).toBeInTheDocument();
    });

    it("renders retry button when onRetry provided", () => {
      render(<ErrorState message="Error" onRetry={() => {}} />);
      expect(screen.getByText("Try Again")).toBeInTheDocument();
    });

    it("does not render retry button when onRetry not provided", () => {
      render(<ErrorState message="Error" />);
      expect(screen.queryByText("Try Again")).not.toBeInTheDocument();
    });
  });

  describe("interaction", () => {
    it("calls onRetry when retry button clicked", async () => {
      const handleRetry = vi.fn();
      const user = userEvent.setup();

      render(<ErrorState message="Error" onRetry={handleRetry} />);

      await user.click(screen.getByText("Try Again"));

      expect(handleRetry).toHaveBeenCalledTimes(1);
    });
  });

  describe("styling", () => {
    it("applies custom className", () => {
      render(<ErrorState message="Error" className="custom-error" />);
      const container = document.querySelector(".custom-error");
      expect(container).toBeInTheDocument();
    });

    it("has text-red-500 class", () => {
      render(<ErrorState message="Error" />);
      const container = document.querySelector(".text-red-500");
      expect(container).toBeInTheDocument();
    });

    it("has text-center class", () => {
      render(<ErrorState message="Error" />);
      const container = document.querySelector(".text-center");
      expect(container).toBeInTheDocument();
    });
  });
});
